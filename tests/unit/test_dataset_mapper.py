"""Unit tests for dataset_mapper filter_records helper."""

from __future__ import annotations

from datetime import date

from google_meridian_mcp_server.meridian.dataset_mapper import filter_records


def _rows():
    return [
        {
            "geo": "us",
            "time": "2023-01-01T00:00:00",
            "media_channel": "tv",
            "media_spend": 5.0,
        },
        {
            "geo": "us",
            "time": "2023-02-01T00:00:00",
            "media_channel": "search",
            "media_spend": 3.0,
        },
        {
            "geo": "ca",
            "time": "2023-02-01T00:00:00",
            "media_channel": "tv",
            "media_spend": 4.0,
        },
    ]


def test_filter_records_by_geo():
    out = filter_records(_rows(), geos=["us"])
    assert {r["geo"] for r in out} == {"us"}


def test_filter_records_by_channel():
    out = filter_records(_rows(), channels=["tv"])
    assert {r["media_channel"] for r in out} == {"tv"}


def test_filter_records_by_date_range():
    out = filter_records(
        _rows(), start_date=date(2023, 2, 1), end_date=date(2023, 2, 28)
    )
    assert all(r["time"].startswith("2023-02") for r in out)


def test_filter_records_ignores_dimension_when_absent():
    rows = [{"kpi": 10.0}, {"kpi": 12.0}]  # no geo/time/channel columns
    assert filter_records(rows, geos=["us"], channels=["tv"]) == rows
