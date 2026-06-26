"""Dataset-to-response transformations for training data and analysis outputs."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

_TIME_COLUMNS = ("time", "media_time")
_CHANNEL_SUFFIX = "_channel"


def _row_time(row: dict) -> date | None:
    for column in _TIME_COLUMNS:
        value = row.get(column)
        if value is not None:
            return pd.Timestamp(value).date()
    return None


def _row_channels(row: dict) -> list[str]:
    names: list[str] = []
    for key, value in row.items():
        if value is None:
            continue
        if key == "channel" or key.endswith(_CHANNEL_SUFFIX):
            names.append(str(value))
    return names


def filter_records(
    records: list[dict],
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    geos: Sequence[str] = (),
    channels: Sequence[str] = (),
) -> list[dict]:
    """Filter row dicts by date range, geo, and channel where those dims exist.

    A row is kept unless it carries the relevant dimension and falls outside the
    requested selection. Rows lacking a dimension are unaffected by that filter.
    """
    geo_set = {str(value) for value in geos}
    channel_set = {str(value) for value in channels}
    out: list[dict] = []
    for row in records:
        if geo_set and "geo" in row and str(row["geo"]) not in geo_set:
            continue
        if channel_set:
            row_channels = _row_channels(row)
            if row_channels and not (set(row_channels) & channel_set):
                continue
        if start_date or end_date:
            row_date = _row_time(row)
            if row_date is not None:
                if start_date and row_date < start_date:
                    continue
                if end_date and row_date > end_date:
                    continue
        out.append(row)
    return out

TRAINING_DATASETS = (
    "kpi",
    "revenue_per_kpi",
    "population",
    "media",
    "media_spend",
    "reach",
    "frequency",
    "rf_spend",
    "organic_media",
    "organic_reach",
    "organic_frequency",
    "non_media_treatments",
    "controls",
)


def extract_training_dataset(mmm: Any, dataset: str) -> list[dict]:
    """Extract a training dataset from a Meridian model as a list of row dicts."""
    return extract_training_datasets(mmm, [dataset])


def extract_training_datasets(mmm: Any, datasets: Sequence[str]) -> list[dict]:
    """Extract and merge multiple training datasets from a Meridian model."""
    if not datasets:
        raise ValueError("At least one dataset must be requested")

    ordered_datasets = list(dict.fromkeys(datasets))
    invalid_datasets = [
        dataset for dataset in ordered_datasets if dataset not in TRAINING_DATASETS
    ]
    if invalid_datasets:
        raise ValueError(f"Unknown dataset '{invalid_datasets[0]}'")

    merged: pd.DataFrame | None = None
    missing_datasets: list[str] = []

    for dataset in ordered_datasets:
        data_array = getattr(mmm.input_data, dataset, None)
        if data_array is None:
            missing_datasets.append(dataset)
            continue

        current = data_array.to_dataframe(name=dataset).reset_index()
        if merged is None:
            merged = current
            continue

        join_keys = [
            column
            for column in current.columns
            if column != dataset and column in merged.columns
        ]
        if not join_keys:
            raise ValueError(
                f"Unable to merge dataset '{dataset}' because it shares no dimension columns with prior selections"
            )
        merged = merged.merge(current, how="outer", on=join_keys)

    if merged is None:
        return []

    for dataset in missing_datasets:
        if dataset not in merged.columns:
            merged[dataset] = None

    ordered_columns = [
        column for column in merged.columns if column not in ordered_datasets
    ] + ordered_datasets
    merged = merged.reindex(columns=ordered_columns)
    return _df_to_records(merged)


def dataset_to_records(ds: Any, var_name: str | None = None) -> list[dict]:
    """Convert an xarray Dataset or DataArray to a list of row dicts."""
    if hasattr(ds, "data_vars"):
        # xr.Dataset
        frames = []
        for vn in ds.data_vars:
            frames.append(ds[vn].to_dataframe(name=vn).reset_index())
        if not frames:
            return []
        result = frames[0]
        for df in frames[1:]:
            result = result.merge(df, how="outer")
        return _df_to_records(result)
    elif hasattr(ds, "to_dataframe"):
        # xr.DataArray
        name = var_name or "value"
        df = ds.to_dataframe(name=name).reset_index()
        return _df_to_records(df)
    elif isinstance(ds, pd.DataFrame):
        return _df_to_records(ds)
    else:
        return []


def _df_to_records(df: pd.DataFrame) -> list[dict]:
    """Convert DataFrame to JSON-safe list of dicts."""
    # Replace NaN with None for JSON serialization
    df = df.where(df.notna(), other=None)
    records = df.to_dict(orient="records")
    # Ensure numpy types are converted to Python types
    clean = []
    for row in records:
        clean.append({k: _to_python(v) for k, v in row.items()})
    return clean


def _to_python(v: Any) -> Any:
    """Convert numpy/pandas types to Python built-ins."""
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return float(v)
    if isinstance(v, (np.bool_,)):
        return bool(v)
    if isinstance(v, np.ndarray):
        return v.tolist()
    if isinstance(v, pd.Timestamp):
        return v.isoformat()
    return v
