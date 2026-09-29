"""Manual QA: time every tool and both optimizers on a realistic full-funnel model.

Fits Google's geo_full_funnel_data.csv (20 geos x 156 weeks) ONCE into a local, gitignored
models/_perf/ root, then drives an in-process server over it. Never committed to CI; the
fitted model stays under the gitignored models/ tree.

  OPTIMIZATION_TIER=local uv run python scripts/qa/full_funnel_perf.py

Prints one JSON document: a timing row per call, peak RSS, and the escalation threshold
(0.5 x ANALYSIS_WORKER_TIMEOUT) with any analysis call that exceeded it.
"""

from __future__ import annotations

import asyncio
import json
import os
import resource
import shutil
import sys
import tempfile
import time
import urllib.request
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DATA_URL = (
    "https://raw.githubusercontent.com/google/meridian/"
    "675da46db5a0fc526e339ca90eddbd9ba18aa74d/meridian/data/simulated_data/csv/"
    "geo_full_funnel_data.csv"
)
ROOT = REPO_ROOT / "models" / "_perf"
EXP = ROOT / "google-full-funnel"
MODEL_ID = "google-full-funnel"
MEDIATOR = "BrandedGQV_control"
SAMPLING = {"n_chains": 2, "n_adapt": 300, "n_burnin": 300, "n_keep": 200}
FUTURE_HORIZON = 13
TERMINAL = ("completed", "failed", "canceled")
POLL_SECONDS = 2
POLL_CAP_SECONDS = 3600


def _fit_once() -> None:
    if (EXP / "model.binpb").exists() and (
        EXP / "mediators" / f"{MEDIATOR}.binpb"
    ).exists():
        return
    import pandas as pd
    from meridian.data import load
    from meridian.model import model, spec
    from meridian.schema.serde import meridian_serde

    csv = ROOT / "geo_full_funnel_data.csv"
    ROOT.mkdir(parents=True, exist_ok=True)
    if not csv.exists():
        urllib.request.urlretrieve(DATA_URL, csv)
    df = pd.read_csv(csv)

    def loader(kpi, channels, **extra):
        media = [f"{c}_impression" for c in channels]
        spend = [f"{c}_spend" for c in channels]
        return load.DataFrameDataLoader(
            df,
            kpi_type="non_revenue",
            coord_to_columns=load.CoordToColumns(
                time="time",
                geo="geo",
                population="population",
                kpi=kpi,
                media=media,
                media_spend=spend,
                **extra,
            ),
            media_to_channel={m: m.replace("_impression", "") for m in media},
            media_spend_to_channel={s: s.replace("_spend", "") for s in spend},
        ).load()

    (EXP / "mediators").mkdir(parents=True, exist_ok=True)
    stage1 = model.Meridian(
        input_data=loader(MEDIATOR, ["AwarenessVideo"]),
        model_spec=spec.ModelSpec(knots=36),
    )
    paid = ["PerformanceNative", "PerformanceDisplay", "AwarenessVideo"]
    stage2 = model.Meridian(
        input_data=loader(
            "conversions",
            paid,
            revenue_per_kpi="revenue_per_conversion",
            controls=["GenericGQV_control"],
            organic_media=[MEDIATOR],
        ),
        model_spec=spec.ModelSpec(
            knots=36,
            saturation_spec={MEDIATOR: "none"},
            population_scaled_controls=["GenericGQV_control"],
        ),
    )
    for m in (stage1, stage2):
        m.sample_prior(n_draws=SAMPLING["n_keep"], seed=0)
        m.sample_posterior(seed=1, **SAMPLING)
    meridian_serde.save_meridian(stage1, str(EXP / "mediators" / f"{MEDIATOR}.binpb"))
    meridian_serde.save_meridian(stage2, str(EXP / "model.binpb"))


# One call per analysis tool / output type (get_reach_frequency is skipped: the model has no
# reach-and-frequency channels).
CALLS = [
    ("get_model_overview", {}),
    ("get_channel_summary", {"output_type": "paid_summary_metrics"}),
    ("get_channel_summary", {"output_type": "roi"}),
    ("get_channel_summary", {"output_type": "baseline_summary_metrics"}),
    ("get_contribution", {"output_type": "contribution_metrics"}),
    ("get_contribution", {"output_type": "contribution_metrics_by_time"}),
    ("get_adstock_decay", {"output_type": "adstock_decay"}),
    ("get_response_curves", {"output_type": "response_curves"}),
    ("get_spend_scenario", {"channel": "AwarenessVideo", "spend_increase": 1000.0}),
    ("get_model_fit", {}),
    ("get_channel_data", {}),
    (
        "get_training_data",
        {
            "dataset": ["kpi"],
            "filters": {"start_date": "2021-01-25", "end_date": "2021-03-29"},
        },
    ),
    ("get_funnel_breakdown", {"output_type": "channel_breakdown"}),
    ("get_funnel_breakdown", {"output_type": "mediator_lift"}),
]


def _peak_mb(who: int) -> int:
    peak = resource.getrusage(who).ru_maxrss
    # macOS reports bytes, Linux kilobytes.
    return round(peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024)


def _size_kb(obj) -> float:
    return round(len(json.dumps(obj, default=str)) / 1024, 1)


async def _wait_terminal(client, run_id: str) -> dict:
    deadline = time.time() + POLL_CAP_SECONDS
    while True:
        status = (
            await client.call_tool("get_optimization_status", {"run_id": run_id})
        ).data
        if status["status"] in TERMINAL or time.time() > deadline:
            return status
        await asyncio.sleep(POLL_SECONDS)


async def _time_optimization(client, tool: str, config: dict) -> dict:
    t = time.time()
    row = {"call": f"{tool} {json.dumps(config)}", "kind": "optimization"}
    submit = (
        await client.call_tool(
            tool, {"model_id": MODEL_ID, "config": config, "force_rerun": True}
        )
    ).data
    if "run_id" not in submit:
        row.update(
            seconds=round(time.time() - t, 1), status="submit_failed", error=submit
        )
        return row
    status = await _wait_terminal(client, submit["run_id"])
    row.update(seconds=round(time.time() - t, 1), status=status["status"])
    if status["status"] == "completed":
        result = (
            await client.call_tool(
                "get_optimization_result", {"run_id": submit["run_id"]}
            )
        ).data
        row["result_kb"] = _size_kb(result)
    else:
        row["error"] = status.get("error")
    return row


async def _run(threshold: float) -> list[dict]:
    from fastmcp import Client

    from google_meridian_mcp_server.server import create_server

    rows = []
    async with Client(create_server()) as client:
        overview = None
        for tool, args in CALLS:
            t = time.time()
            row = {"call": f"{tool} {json.dumps(args)}", "kind": "analysis"}
            try:
                res = await client.call_tool(tool, {"model_id": MODEL_ID, **args})
                data = res.data
                row["seconds"] = round(time.time() - t, 1)
                row["response_kb"] = _size_kb(data)
                if isinstance(data, dict) and "error_code" in data:
                    row["error"] = data
                if tool == "get_model_overview":
                    overview = data
            except Exception as exc:  # noqa: BLE001 - QA driver: record and continue
                row["seconds"] = round(time.time() - t, 1)
                row["error"] = repr(exc)
            row["over_threshold"] = row["seconds"] > threshold
            rows.append(row)

        rows.append(
            await _time_optimization(
                client, "run_optimization", {"scenario": {"type": "fixed_budget"}}
            )
        )

        last = date.fromisoformat(str(overview["time"]["end"])[:10])
        dates = sorted(
            date.fromisoformat(str(v)[:10]) for v in overview["time"]["values"]
        )
        cadence = (dates[1] - dates[0]).days
        future = {
            "scenario": {"type": "fixed_budget"},
            "future": {
                "start_date": (last + timedelta(days=cadence)).isoformat(),
                "horizon": FUTURE_HORIZON,
                "reference": {"mode": "trailing"},
            },
        }
        rows.append(await _time_optimization(client, "run_future_optimization", future))
    return rows


def main() -> None:
    _fit_once()
    runs_root = tempfile.mkdtemp(prefix="ff-perf-runs-")
    os.environ.update(
        PERSISTENCE_BACKEND="local",
        LOCAL_MODELS_ROOT=str(ROOT),
        OPTIMIZATION_TIER="local",
        RESULT_CACHE_ENABLED="false",
        OPTIMIZATION_RUNS_ROOT=runs_root,
    )
    # Import after the environment is set: config.py reads .env (which points at GCS)
    # without overriding variables that are already set.
    from google_meridian_mcp_server.config import load_config

    timeout = load_config().analysis_worker_timeout
    threshold = 0.5 * timeout
    try:
        rows = asyncio.run(_run(threshold))
    finally:
        shutil.rmtree(runs_root, ignore_errors=True)
    print(
        json.dumps(
            {
                "analysis_worker_timeout": timeout,
                "escalation_threshold_seconds": threshold,
                "over_threshold": [r["call"] for r in rows if r.get("over_threshold")],
                "rows": rows,
                "peak_server_process_rss_mb": _peak_mb(resource.RUSAGE_SELF),
                "peak_largest_child_rss_mb": _peak_mb(resource.RUSAGE_CHILDREN),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
