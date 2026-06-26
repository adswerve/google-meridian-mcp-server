"""Model metadata extraction and shared Meridian helpers."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterable
from typing import Any

import numpy as np
import pandas as pd

from google_meridian_mcp_server.domain.filters import AnalysisFilters
from google_meridian_mcp_server.meridian.dataset_mapper import (
    TRAINING_DATASETS,
    dataset_to_records,
)


class MeridianInterrogator:
    """Shared accessors over a loaded Meridian model."""

    def __init__(self, mmm: Any) -> None:
        self._mmm = mmm
        self._analyzer = None

    def _get_analyzer(self):
        if self._analyzer is None:
            from meridian.analysis import analyzer as analyzer_mod

            self._analyzer = analyzer_mod.Analyzer(self._mmm)
        return self._analyzer

    def is_national(self) -> bool:
        value = getattr(self._mmm, "is_national", False)
        return bool(value() if callable(value) else value)

    def has_revenue_per_kpi(self) -> bool:
        return getattr(self._mmm.input_data, "revenue_per_kpi", None) is not None

    def has_rf_channels(self) -> bool:
        return len(self.get_data_inputs()["rf_media"]) > 0

    def resolve_use_kpi(self, filters: AnalysisFilters) -> bool:
        if filters.use_kpi is not None:
            return filters.use_kpi
        return not self.has_revenue_per_kpi()

    def get_geos_info(self) -> pd.DataFrame:
        input_data = self._mmm.input_data
        geos = getattr(input_data, "geo", None)
        population = getattr(input_data, "population", None)
        if geos is None or population is None:
            return pd.DataFrame(columns=["geo", "population"])

        return pd.DataFrame(
            {
                "geo": self._coord_values("geo"),
                "population": self._numeric_values(population),
            }
        ).sort_values("population", ascending=False, ignore_index=True)

    def get_time_values(self) -> list[str]:
        return [str(value) for value in self._raw_coord_values("time")]

    def get_data_inputs(self) -> dict[str, list[str]]:
        return {
            "media": self._coord_values("media_channel"),
            "rf_media": self._coord_values("rf_channel"),
            "non_media": self._coord_values("non_media_channel"),
            "organic_media": self._coord_values("organic_media_channel"),
            "organic_rf_media": self._coord_values("organic_rf_channel"),
            "controls": self._coord_values("control_variable"),
        }

    def get_data_schema(self, include_kpi: bool = False) -> dict[str, dict | list[str]]:
        input_data = self._mmm.input_data
        media_channels = self._coord_values("media_channel")
        rf_channels = self._coord_values("rf_channel")

        schema: dict[str, dict | list[str]] = OrderedDict(
            {
                "media": OrderedDict(
                    {
                        "impressions": media_channels,
                        "spend": [f"{channel}_spend" for channel in media_channels],
                    }
                ),
                "rf_media": OrderedDict(
                    {
                        "reach": [f"{channel}_reach" for channel in rf_channels],
                        "frequency": [
                            f"{channel}_frequency" for channel in rf_channels
                        ],
                        "spend": [f"{channel}_rf_spend" for channel in rf_channels],
                    }
                ),
                "non_media": self._coord_values("non_media_channel"),
                "organic_media": self._coord_values("organic_media_channel"),
                "organic_rf_media": self._coord_values("organic_rf_channel"),
                "controls": self._coord_values("control_variable"),
            }
        )

        if include_kpi:
            schema["kpi"] = (
                ["kpi"] if getattr(input_data, "kpi", None) is not None else []
            )
            schema["population"] = (
                ["population"]
                if getattr(input_data, "population", None) is not None
                else []
            )
            schema["revenue_per_kpi"] = (
                ["revenue_per_kpi"]
                if getattr(input_data, "revenue_per_kpi", None) is not None
                else []
            )

        return schema

    def get_available_training_datasets(self) -> list[str]:
        input_data = self._mmm.input_data
        return [
            dataset
            for dataset in TRAINING_DATASETS
            if getattr(input_data, dataset, None) is not None
        ]

    def get_input_column_names(self, include_kpi: bool = True) -> list[str]:
        return self._flatten_schema_values(
            self.get_data_schema(include_kpi=include_kpi)
        )

    def get_model_overview(self) -> dict[str, Any]:
        time_values = self.get_time_values()
        data_inputs = self.get_data_inputs()
        media_channels = data_inputs["media"]
        rf_channels = data_inputs["rf_media"]
        organic_media = data_inputs["organic_media"]
        organic_rf = data_inputs["organic_rf_media"]
        geo_rows = dataset_to_records(self.get_geos_info())
        has_kpi = getattr(self._mmm.input_data, "kpi", None) is not None
        has_revenue = getattr(self._mmm.input_data, "revenue_per_kpi", None) is not None
        metric_views: list[str] = []
        if has_kpi:
            metric_views.append("kpi")
        if has_revenue:
            metric_views.append("revenue")

        return {
            "model_type": "national" if self.is_national() else "geo",
            "is_national": self.is_national(),
            "time": {
                "start": time_values[0] if time_values else None,
                "end": time_values[-1] if time_values else None,
                "count": len(time_values),
                "values": time_values,
            },
            "geo_count": len(geo_rows),
            "geo_names": [str(row["geo"]) for row in geo_rows],
            "total_population": (
                sum(row["population"] for row in geo_rows) if geo_rows else None
            ),
            "geos": geo_rows,
            "data_inputs": data_inputs,
            "media_channels": media_channels,
            "rf_channels": rf_channels,
            "total_channels": len(media_channels) + len(rf_channels),
            "organic_media": organic_media,
            "organic_rf": organic_rf,
            "data_schema": self.get_data_schema(include_kpi=True),
            "input_column_names": self.get_input_column_names(include_kpi=True),
            "available_training_datasets": self.get_available_training_datasets(),
            "metric_views": metric_views,
            "has_revenue_per_kpi": has_revenue,
        }

    def get_data(
        self,
        agg_geos: bool = True,
        geos: list[str] | None = None,
        dt_start: str | None = None,
        dt_end: str | None = None,
    ) -> pd.DataFrame:
        """Return the model input data as one wide dataframe."""
        input_data = self._mmm.input_data
        frames: list[pd.DataFrame] = []

        if getattr(input_data, "kpi", None) is not None:
            frames.append(self._to_dataframe(input_data.kpi, "kpi"))
        if getattr(input_data, "revenue_per_kpi", None) is not None:
            frames.append(
                self._to_dataframe(input_data.revenue_per_kpi, "revenue_per_kpi")
            )
        if getattr(input_data, "population", None) is not None:
            frames.append(self._build_population_df())
        if getattr(input_data, "media", None) is not None:
            frames.extend(self._extract_media_data())
        if getattr(input_data, "reach", None) is not None:
            frames.extend(self._extract_rf_media_data())
        if getattr(input_data, "organic_media", None) is not None:
            frames.extend(self._extract_organic_media_data())
        if getattr(input_data, "organic_reach", None) is not None:
            frames.extend(self._extract_organic_rf_data())
        if getattr(input_data, "non_media_treatments", None) is not None:
            frames.extend(self._extract_non_media_data())
        if getattr(input_data, "controls", None) is not None:
            frames.extend(self._extract_controls_data())

        if not frames:
            return pd.DataFrame()

        data = pd.concat(frames, axis=1)
        if isinstance(data.index, pd.MultiIndex):
            data = data.sort_index(level=["time", "geo"], ascending=[True, True])

        if geos is not None and isinstance(data.index, pd.MultiIndex):
            data = data[data.index.get_level_values("geo").isin(geos)]

        if dt_start is not None or dt_end is not None:
            data = self._filter_data_by_date_range(data, dt_start, dt_end)

        if agg_geos and isinstance(data.index, pd.MultiIndex):
            data = data.groupby(level="time").sum()

        return data

    def _coord_values(self, attr: str) -> list[str]:
        return [str(value) for value in self._raw_coord_values(attr)]

    def _build_population_df(self) -> pd.DataFrame:
        input_data = self._mmm.input_data
        time_df = self._to_dataframe(input_data.time, "time").reset_index(drop=True)
        population_df = self._to_dataframe(
            input_data.population, "population"
        ).reset_index()
        time_df["join_key"] = 0
        population_df["join_key"] = 0
        population = time_df.merge(population_df, on="join_key", how="outer")
        population.set_index(["geo", "time"], inplace=True)
        return population.drop(columns="join_key")

    def _extract_media_data(self) -> list[pd.DataFrame]:
        input_data = self._mmm.input_data
        channels = self._coord_values("media_channel")
        result: list[pd.DataFrame] = []

        media_df = self._to_dataframe(input_data.media, "media").reset_index()
        for channel in channels:
            channel_df = media_df[media_df.media_channel == channel].copy()
            channel_df[channel] = channel_df["media"]
            channel_df["time"] = channel_df["media_time"]
            channel_df.drop(
                columns=["media_channel", "media", "media_time"], inplace=True
            )
            channel_df.set_index(["geo", "time"], inplace=True)
            result.append(channel_df)

        spend_df = self._to_dataframe(
            input_data.media_spend, "media_spend"
        ).reset_index()
        for channel in channels:
            channel_df = spend_df[spend_df.media_channel == channel].copy()
            channel_df[f"{channel}_spend"] = channel_df["media_spend"]
            channel_df.drop(columns=["media_channel", "media_spend"], inplace=True)
            channel_df.set_index(["geo", "time"], inplace=True)
            result.append(channel_df)

        return result

    def _extract_rf_media_data(self) -> list[pd.DataFrame]:
        input_data = self._mmm.input_data
        channels = self._coord_values("rf_channel")
        result: list[pd.DataFrame] = []

        if input_data.reach is not None:
            reach_df = self._to_dataframe(input_data.reach, "reach").reset_index()
            for channel in channels:
                channel_df = reach_df[reach_df.rf_channel == channel].copy()
                channel_df[f"{channel}_reach"] = channel_df["reach"]
                if "media_time" in channel_df.columns:
                    channel_df["time"] = channel_df["media_time"]
                    channel_df.drop(columns=["media_time"], inplace=True)
                channel_df.drop(columns=["rf_channel", "reach"], inplace=True)
                channel_df.set_index(["geo", "time"], inplace=True)
                result.append(channel_df)

        if input_data.frequency is not None:
            frequency_df = self._to_dataframe(
                input_data.frequency, "frequency"
            ).reset_index()
            for channel in channels:
                channel_df = frequency_df[frequency_df.rf_channel == channel].copy()
                channel_df[f"{channel}_frequency"] = channel_df["frequency"]
                if "media_time" in channel_df.columns:
                    channel_df["time"] = channel_df["media_time"]
                    channel_df.drop(columns=["media_time"], inplace=True)
                channel_df.drop(columns=["rf_channel", "frequency"], inplace=True)
                channel_df.set_index(["geo", "time"], inplace=True)
                result.append(channel_df)

        if input_data.rf_spend is not None:
            spend_df = self._to_dataframe(input_data.rf_spend, "rf_spend").reset_index()
            for channel in channels:
                channel_df = spend_df[spend_df.rf_channel == channel].copy()
                channel_df[f"{channel}_rf_spend"] = channel_df["rf_spend"]
                channel_df.drop(columns=["rf_channel", "rf_spend"], inplace=True)
                channel_df.set_index(["geo", "time"], inplace=True)
                result.append(channel_df)

        return result

    def _extract_organic_media_data(self) -> list[pd.DataFrame]:
        input_data = self._mmm.input_data
        channels = self._coord_values("organic_media_channel")
        organic_df = self._to_dataframe(
            input_data.organic_media, "organic_media"
        ).reset_index()
        result: list[pd.DataFrame] = []

        for channel in channels:
            channel_df = organic_df[organic_df.organic_media_channel == channel].copy()
            channel_df[channel] = channel_df["organic_media"]
            channel_df["time"] = channel_df["media_time"]
            channel_df.drop(
                columns=["organic_media_channel", "organic_media", "media_time"],
                inplace=True,
            )
            channel_df.set_index(["geo", "time"], inplace=True)
            result.append(channel_df)

        return result

    def _extract_organic_rf_data(self) -> list[pd.DataFrame]:
        input_data = self._mmm.input_data
        channels = self._coord_values("organic_rf_channel")
        result: list[pd.DataFrame] = []

        if input_data.organic_reach is not None:
            reach_df = self._to_dataframe(
                input_data.organic_reach, "organic_reach"
            ).reset_index()
            for channel in channels:
                channel_df = reach_df[reach_df.organic_rf_channel == channel].copy()
                channel_df[f"{channel}_organic_reach"] = channel_df["organic_reach"]
                if "media_time" in channel_df.columns:
                    channel_df["time"] = channel_df["media_time"]
                    channel_df.drop(columns=["media_time"], inplace=True)
                channel_df.drop(
                    columns=["organic_rf_channel", "organic_reach"], inplace=True
                )
                channel_df.set_index(["geo", "time"], inplace=True)
                result.append(channel_df)

        if input_data.organic_frequency is not None:
            frequency_df = self._to_dataframe(
                input_data.organic_frequency, "organic_frequency"
            ).reset_index()
            for channel in channels:
                channel_df = frequency_df[
                    frequency_df.organic_rf_channel == channel
                ].copy()
                channel_df[f"{channel}_organic_frequency"] = channel_df[
                    "organic_frequency"
                ]
                if "media_time" in channel_df.columns:
                    channel_df["time"] = channel_df["media_time"]
                    channel_df.drop(columns=["media_time"], inplace=True)
                channel_df.drop(
                    columns=["organic_rf_channel", "organic_frequency"], inplace=True
                )
                channel_df.set_index(["geo", "time"], inplace=True)
                result.append(channel_df)

        return result

    def _extract_non_media_data(self) -> list[pd.DataFrame]:
        input_data = self._mmm.input_data
        channels = self._coord_values("non_media_channel")
        non_media_df = self._to_dataframe(
            input_data.non_media_treatments, "non_media_treatments"
        ).reset_index()
        result: list[pd.DataFrame] = []

        for channel in channels:
            channel_df = non_media_df[non_media_df.non_media_channel == channel].copy()
            channel_df[channel] = channel_df["non_media_treatments"]
            channel_df.drop(
                columns=["non_media_channel", "non_media_treatments"], inplace=True
            )
            channel_df.set_index(["geo", "time"], inplace=True)
            result.append(channel_df)

        return result

    def _extract_controls_data(self) -> list[pd.DataFrame]:
        input_data = self._mmm.input_data
        channels = self._coord_values("control_variable")
        controls_df = self._to_dataframe(input_data.controls, "controls").reset_index()
        result: list[pd.DataFrame] = []

        for channel in channels:
            channel_df = controls_df[controls_df.control_variable == channel].copy()
            channel_df[channel] = channel_df["controls"]
            channel_df.drop(columns=["control_variable", "controls"], inplace=True)
            channel_df.set_index(["geo", "time"], inplace=True)
            result.append(channel_df)

        return result

    def _raw_coord_values(self, attr: str) -> list[Any]:
        values = getattr(self._mmm.input_data, attr, None)
        if values is None:
            return []

        raw = np.asarray(values).reshape(-1).tolist()
        if not isinstance(raw, list):
            raw = [raw]
        return [self._to_python(value) for value in raw]

    def _numeric_values(self, values: Any) -> list[int | float]:
        numeric = []
        for value in np.asarray(values).reshape(-1).tolist():
            parsed = self._to_python(value)
            numeric.append(parsed if isinstance(parsed, (int, float)) else 0)
        return numeric

    def _flatten_schema_values(self, schema: dict[str, dict | list[str]]) -> list[str]:
        flattened: list[str] = []

        def _append(values: Iterable[str]) -> None:
            for value in values:
                if value not in flattened:
                    flattened.append(value)

        for value in schema.values():
            if isinstance(value, dict):
                for nested in value.values():
                    _append(nested)
                continue
            _append(value)

        return flattened

    @staticmethod
    def _filter_data_by_date_range(
        data: pd.DataFrame, dt_start: str | None, dt_end: str | None
    ) -> pd.DataFrame:
        if data.empty:
            return data

        if isinstance(data.index, pd.MultiIndex):
            time_values = pd.to_datetime(data.index.get_level_values("time"))
        else:
            time_values = pd.to_datetime(data.index)

        mask = pd.Series(True, index=data.index)
        if dt_start is not None:
            mask &= time_values >= pd.Timestamp(dt_start)
        if dt_end is not None:
            mask &= time_values <= pd.Timestamp(dt_end)

        return data.loc[mask.to_numpy()]

    @staticmethod
    def _to_dataframe(data: Any, name: str) -> pd.DataFrame:
        try:
            return data.to_dataframe(name=name)
        except TypeError:
            return data.to_dataframe()

    @staticmethod
    def _to_python(value: Any) -> Any:
        if isinstance(value, pd.Timestamp):
            return value.isoformat()
        if isinstance(value, np.datetime64):
            return pd.Timestamp(value).isoformat()
        if isinstance(value, np.integer):
            return int(value)
        if isinstance(value, np.floating):
            return float(value)
        if isinstance(value, np.bool_):
            return bool(value)
        return value
