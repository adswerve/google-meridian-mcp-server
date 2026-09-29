"""Generate dummy Meridian models for live validation across all variants.

Builds 7 fixtures: the 2x3 (national|geo) x (revenue|kpi_rpk|kpi_only) matrix
(all with reach & frequency channels) plus one media-only geo-revenue model so
the no-RF graceful-error path is exercised. Each model is built from synthetic
data, fitted with a tiny real posterior, and serialized to .binpb. Pickle
(.pkl) models are no longer supported by this server -- see
reports/pkl-format-removed.md -- so no .pkl fixture is produced here.

It also builds one full-funnel fixture (`geo-full-funnel`: a KPI model plus two
mediator models, no RF), kept out of `VARIANTS` so the drift harness's fixture
set stays at seven.

Usage:
  uv run python scripts/generate_validation_models.py            # build if missing
  uv run python scripts/generate_validation_models.py --force    # rebuild all
  uv run python scripts/generate_validation_models.py --out DIR  # custom out dir
"""

from __future__ import annotations

import argparse
import dataclasses
from pathlib import Path

import numpy as np
import pandas as pd

DEFAULT_OUT_ROOT = Path("models/_validation")

# Small but valid fit. Keep n_media_times >= n_times (random_dataset back-dates).
N_TIMES = 52
N_MEDIA_TIMES = 55
N_MEDIA_CHANNELS = 3
N_RF_CHANNELS = 2
N_ORGANIC_MEDIA = 1
N_ORGANIC_RF = 1
N_NON_MEDIA = 1
N_CONTROLS = 2
PRIOR_DRAWS = 10
POSTERIOR_KW = {"n_chains": 1, "n_adapt": 10, "n_burnin": 10, "n_keep": 10}


@dataclasses.dataclass(frozen=True)
class VariantSpec:
    key: str
    factory: str  # "revenue" | "kpi_rpk" | "kpi_only"
    n_geos: int
    with_rf: bool

    def factory_has_revenue(self) -> bool:
        # revenue and kpi_rpk variants carry revenue_per_kpi; kpi_only does not.
        return self.factory in ("revenue", "kpi_rpk")


VARIANTS: list[VariantSpec] = [
    VariantSpec("national-revenue", "revenue", 1, True),
    VariantSpec("geo-revenue", "revenue", 5, True),
    VariantSpec("national-kpi-rpk", "kpi_rpk", 1, True),
    VariantSpec("geo-kpi-rpk", "kpi_rpk", 5, True),
    VariantSpec("national-kpi-only", "kpi_only", 1, True),
    VariantSpec("geo-kpi-only", "kpi_only", 5, True),
    VariantSpec("geo-revenue-media-only", "revenue", 5, False),
]

_FACTORY_NAMES = {
    "revenue": "sample_input_data_revenue",
    "kpi_rpk": "sample_input_data_non_revenue_revenue_per_kpi",
    "kpi_only": "sample_input_data_non_revenue_no_revenue_per_kpi",
}


FF_PAID = ("A", "B", "C")
FF_KNOTS = 8


@dataclasses.dataclass(frozen=True)
class FullFunnelVariantSpec:
    """A KPI model plus one stage-1 model per (mediator, driving paid channels)."""

    key: str
    n_geos: int
    mediators: tuple[tuple[str, tuple[str, ...]], ...]
    with_rf: bool = False  # AnalyzerFullFunnel cannot handle RF (known limitation)
    factory: str = "full_funnel"

    def factory_has_revenue(self) -> bool:
        return True

    def mediator_names(self) -> tuple[str, ...]:
        return tuple(name for name, _ in self.mediators)


# Kept OUT of VARIANTS: matrix.fixture_specs() mirrors VARIANTS, and the drift
# harness must keep diffing the same seven fixtures (spec section 8).
FULL_FUNNEL_VARIANTS: list[FullFunnelVariantSpec] = [
    FullFunnelVariantSpec("geo-full-funnel", 5, (("M1", ("A",)), ("M2", ("A", "B")))),
]


def _full_funnel_frame(spec: FullFunnelVariantSpec) -> pd.DataFrame:
    """Synthetic geo x week data with a known brand path (A -> M1; A, B -> M2)."""
    rng = np.random.default_rng(7)
    times = pd.date_range("2023-01-02", periods=N_TIMES, freq="7D").strftime("%Y-%m-%d")
    frames = []
    for g in range(spec.n_geos):
        scale = float(g + 1)
        imp = {c: rng.gamma(4.0, 2500.0 * scale, N_TIMES) for c in FF_PAID}
        m1 = 400.0 * scale + 0.02 * imp["A"] + rng.normal(0.0, 20.0, N_TIMES)
        m2 = (
            300.0 * scale
            + 0.01 * imp["A"]
            + 0.015 * imp["B"]
            + rng.normal(0.0, 20.0, N_TIMES)
        )
        control = rng.normal(0.0, 1.0, N_TIMES)
        kpi = (
            1000.0 * scale
            + 0.004 * imp["A"]
            + 0.006 * imp["B"]
            + 0.008 * imp["C"]
            + 0.5 * m1
            + 0.4 * m2
            + 20.0 * control
            + rng.normal(0.0, 40.0, N_TIMES)
        )
        frame = pd.DataFrame(
            {
                "geo": f"geo_{g}",
                "time": times,
                "population": 100_000.0 * scale,
                "kpi": np.clip(kpi, 1.0, None),
                "revenue_per_kpi": 2.0,
                "control": control,
                "M1": np.clip(m1, 1.0, None),
                "M2": np.clip(m2, 1.0, None),
            }
        )
        for c in FF_PAID:
            frame[f"{c}_impression"] = imp[c]
            frame[f"{c}_spend"] = imp[c] * 0.02
        frames.append(frame)
    return pd.concat(frames, ignore_index=True)


def _ff_input_data(df, *, kpi: str, media_channels, **extra):
    from meridian.data import load

    media = [f"{c}_impression" for c in media_channels]
    spend = [f"{c}_spend" for c in media_channels]
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
        media_to_channel={f"{c}_impression": c for c in media_channels},
        media_spend_to_channel={f"{c}_spend": c for c in media_channels},
    ).load()


def _ff_model_spec(*, saturation_spec=None):
    from meridian.model import spec as spec_mod

    kwargs = {"knots": FF_KNOTS}
    if saturation_spec is not None:
        kwargs["saturation_spec"] = saturation_spec
    return spec_mod.ModelSpec(**kwargs)


def _fit_with_spec(input_data, model_spec):
    from meridian.model import model

    mmm = model.Meridian(input_data=input_data, model_spec=model_spec)
    mmm.sample_prior(n_draws=PRIOR_DRAWS, seed=0)
    mmm.sample_posterior(seed=1, **POSTERIOR_KW)
    return mmm


def _save(mmm, path: Path) -> None:
    from meridian.schema.serde import meridian_serde

    meridian_serde.save_meridian(mmm, str(path))


def build_full_funnel_variant(
    spec: FullFunnelVariantSpec, out_root: Path = DEFAULT_OUT_ROOT, force: bool = False
) -> Path:
    """Fit and save mediators FIRST and model.binpb LAST.

    "Already built" means model.binpb AND every mediator file exist, so an interrupted
    build (model.binpb absent, or a mediator missing) is rebuilt rather than skipped.
    Identical PRIOR_DRAWS/POSTERIOR_KW across all stages: AnalyzerFullFunnel pairs
    posterior draws by index.
    """
    target_dir = out_root / spec.key
    stage2_path = target_dir / "model.binpb"
    mediator_paths = {
        name: target_dir / "mediators" / f"{name}.binpb" for name, _ in spec.mediators
    }
    if (
        not force
        and stage2_path.exists()
        and all(p.exists() for p in mediator_paths.values())
    ):
        print(f"  skip {spec.key} (exists)")
        return stage2_path
    (target_dir / "mediators").mkdir(parents=True, exist_ok=True)
    stage2_path.unlink(missing_ok=True)  # never leave a stale "complete" marker
    df = _full_funnel_frame(spec)
    for name, channels in spec.mediators:
        stage1 = _fit_with_spec(
            _ff_input_data(df, kpi=name, media_channels=channels), _ff_model_spec()
        )
        _save(stage1, mediator_paths[name])
    stage2 = _fit_with_spec(
        _ff_input_data(
            df,
            kpi="kpi",
            media_channels=FF_PAID,
            revenue_per_kpi="revenue_per_kpi",
            controls=["control"],
            organic_media=list(spec.mediator_names()),
        ),
        _ff_model_spec(saturation_spec={n: "none" for n in spec.mediator_names()}),
    )
    _save(stage2, stage2_path)
    print(f"  built {spec.key} -> {stage2_path} (+{len(mediator_paths)} mediators)")
    return stage2_path


def _build_input_data(spec: VariantSpec):
    from meridian.data import test_utils

    factory = getattr(test_utils, _FACTORY_NAMES[spec.factory])
    kwargs = dict(
        n_geos=spec.n_geos,
        n_times=N_TIMES,
        n_media_times=N_MEDIA_TIMES,
        n_controls=N_CONTROLS,
        n_media_channels=N_MEDIA_CHANNELS,
        n_organic_media_channels=N_ORGANIC_MEDIA,
        n_non_media_channels=N_NON_MEDIA,
        seed=0,
    )
    if spec.with_rf:
        kwargs["n_rf_channels"] = N_RF_CHANNELS
        kwargs["n_organic_rf_channels"] = N_ORGANIC_RF
    return factory(**kwargs)


def _fit(input_data):
    from meridian.model import model, spec

    mmm = model.Meridian(input_data=input_data, model_spec=spec.ModelSpec())
    mmm.sample_prior(n_draws=PRIOR_DRAWS, seed=0)
    mmm.sample_posterior(seed=1, **POSTERIOR_KW)
    return mmm


def build_variant(
    variant: VariantSpec, out_root: Path = DEFAULT_OUT_ROOT, force: bool = False
) -> Path:
    from meridian.schema.serde import meridian_serde

    target_dir = out_root / variant.key
    target = target_dir / "model.binpb"
    if target.exists() and not force:
        print(f"  skip {variant.key} (exists)")
        return target
    target_dir.mkdir(parents=True, exist_ok=True)
    mmm = _fit(_build_input_data(variant))
    meridian_serde.save_meridian(mmm, str(target))
    print(f"  built {variant.key} -> {target}")
    return target


def build_all(out_root: Path = DEFAULT_OUT_ROOT, force: bool = False) -> list[Path]:
    print(f"Generating validation fixtures in {out_root} (force={force})")
    paths = [build_variant(variant, out_root, force) for variant in VARIANTS]
    paths += [
        build_full_funnel_variant(spec, out_root, force)
        for spec in FULL_FUNNEL_VARIANTS
    ]
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force", action="store_true", help="Rebuild existing fixtures"
    )
    parser.add_argument("--out", default=str(DEFAULT_OUT_ROOT), help="Output directory")
    args = parser.parse_args()
    build_all(Path(args.out), force=args.force)


if __name__ == "__main__":
    main()
