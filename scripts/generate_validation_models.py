"""Generate dummy Meridian models for live validation across all variants.

Builds 7 fixtures: the 2x3 (national|geo) x (revenue|kpi_rpk|kpi_only) matrix
(all with reach & frequency channels) plus one media-only geo-revenue model so
the no-RF graceful-error path is exercised. Each model is built from synthetic
data, fitted with a tiny real posterior, and serialized to .binpb (one variant
also to .pkl to exercise the loader's pickle path).

Usage:
  uv run python scripts/generate_validation_models.py            # build if missing
  uv run python scripts/generate_validation_models.py --force    # rebuild all
  uv run python scripts/generate_validation_models.py --out DIR  # custom out dir
"""

from __future__ import annotations

import argparse
import dataclasses
from pathlib import Path

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
    # Exercise the loader's pickle branch with one extra .pkl fixture.
    if variant.key == "national-revenue":
        from meridian.model import model as model_mod

        pkl_dir = out_root / "national-revenue-pkl"
        pkl_dir.mkdir(parents=True, exist_ok=True)
        model_mod.save_mmm(mmm, str(pkl_dir / "model.pkl"))
        print(f"  built national-revenue-pkl -> {pkl_dir / 'model.pkl'}")
    return target


def build_all(out_root: Path = DEFAULT_OUT_ROOT, force: bool = False) -> list[Path]:
    print(f"Generating validation fixtures in {out_root} (force={force})")
    return [build_variant(variant, out_root, force) for variant in VARIANTS]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="Rebuild existing fixtures")
    parser.add_argument("--out", default=str(DEFAULT_OUT_ROOT), help="Output directory")
    args = parser.parse_args()
    build_all(Path(args.out), force=args.force)


if __name__ == "__main__":
    main()
