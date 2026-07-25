"""Materialize the reviewed ERA5-Land monthly North America acquisition plan."""

from __future__ import annotations

import calendar
import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
NASA_PLAN_PATH = REPOSITORY_ROOT / "infra/local-warehouse/plans/nasa-power-na-sampling-20220430-20260430.json"
ERA5_PLAN_PATH = REPOSITORY_ROOT / "infra/local-warehouse/plans/era5-land-na-sampling-20220430-20260430.json"
WINDOW_START = date(2022, 4, 30)
WINDOW_END = date(2026, 4, 30)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _monthly_periods(start: date, end: date) -> list[dict[str, object]]:
    periods: list[dict[str, object]] = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        first = date(year, month, 1)
        last = date(year, month, calendar.monthrange(year, month)[1])
        period_start = max(start, first)
        period_end = min(end, last)
        periods.append(
            {
                "key": f"{year:04d}-{month:02d}",
                "start_date": period_start.isoformat(),
                "end_date": period_end.isoformat(),
                "year": f"{year:04d}",
                "month": f"{month:02d}",
                "days": [f"{day:02d}" for day in range(period_start.day, period_end.day + 1)],
            }
        )
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return periods


def main() -> None:
    nasa_plan = json.loads(NASA_PLAN_PATH.read_text(encoding="utf-8"))
    cells = nasa_plan["nasa"]["cells"]
    if len(cells) != 2_980:
        raise ValueError("the reviewed NASA North America lattice must contain exactly 2,980 cells")
    if any(cell["latitude"] % 1 or cell["longitude"] % 1 for cell in cells):
        raise ValueError("the ERA5 requested output grid requires integral-degree sampling coordinates")
    nasa_plan_checksum = hashlib.sha256(_canonical_bytes(nasa_plan)).hexdigest()
    plan = {
        "schema_version": "era5-land-daily-v1",
        "source": {
            "key": "era5-land",
            "name": "ERA5-Land post-processed daily statistics",
            "owner": "Copernicus Climate Change Service",
            "purpose": "Approved North America retrospective land-state baseline.",
            "base_url": "https://cds.climate.copernicus.eu/datasets/derived-era5-land-daily-statistics",
            "license_name": "Copernicus Climate Change Service terms of use",
            "license_url": "https://cds.climate.copernicus.eu/terms",
            "citation": "ERA5-Land post-processed daily statistics from 1950 to present, Copernicus Climate Change Service Climate Data Store.",
            "reviewed_at": "2026-07-21T00:00:00Z",
            "reviewed_by": "data-governance",
        },
        "window": {"start_date": WINDOW_START.isoformat(), "end_date": WINDOW_END.isoformat()},
        "dataset": "derived-era5-land-daily-statistics",
        "daily_statistic": "daily_mean",
        "frequency": "1_hourly",
        "time_zone": "utc+00:00",
        "requested_grid_degrees": 1.0,
        "requested_area": {"north": 84, "west": -170, "south": 14, "east": -50},
        "native_grid_name": "era5-land-0.1-degree",
        "native_grid_resolution_m": 9_000,
        "cells": cells,
        "parameters": [
            "10m_u_component_of_wind",
            "10m_v_component_of_wind",
            "2m_dewpoint_temperature",
            "2m_temperature",
            "soil_temperature_level_1",
            "volumetric_soil_water_layer_1",
        ],
        "periods": _monthly_periods(WINDOW_START, WINDOW_END),
        "nasa_lattice_plan_checksum": nasa_plan_checksum,
        "terms_acceptance_required": True,
        "transform_version": "era5-land-daily-requested-grid-normalization-v1",
        "release_set_key": "era5-land-na-20220430-20260430-asof-20260721",
        "release_set_as_of": datetime(2026, 7, 21, 23, 59, 59, tzinfo=UTC).isoformat().replace("+00:00", "Z"),
        "description": (
            "Four-year North America ERA5-Land daily-mean land-state samples on the reviewed NASA one-degree lattice. "
            "The CDS request explicitly resamples the 0.1-degree source to a one-degree output grid; it is point-sample "
            "context, never acre-scale data. The operator must accept the CDS terms and provide CDS credentials before execution."
        ),
    }
    ERA5_PLAN_PATH.write_bytes(_canonical_bytes(plan) + b"\n")
    print(ERA5_PLAN_PATH)


if __name__ == "__main__":
    main()
