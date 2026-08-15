"""Author AgERA5 agrometeorological indicators plan.

Usage:
    python plans/author_agera5_plans.py
"""

from __future__ import annotations

import sys
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVICE_ROOT / "src"))

from agri_data_service.execution.contracts import canonical_json_bytes  # noqa: E402
from agri_data_service.execution.historical_agera5 import (  # noqa: E402
    AGERA5_DATASET_ID,
    AGERA5_DATASET_VERSION,
    AGERA5_SIGNAL_SPECIFICATIONS,
    HistoricalAgera5BackfillPlan,
)

PLAN_OUTPUT_PATH = SERVICE_ROOT / "plans" / "cds-agera5-pnw-20220430-20260430.json"


def build_agera5_plan() -> HistoricalAgera5BackfillPlan:
    return HistoricalAgera5BackfillPlan.model_validate(
        {
            "source": {
                "key": "copernicus-cds-agera5",
                "name": "AgERA5 agrometeorological indicators from 1979 to present",
                "owner": "Copernicus Climate Change Service",
                "purpose": "Pacific Northwest agrometeorological indicators baseline",
                "base_url": "https://cds.climate.copernicus.eu/datasets/sis-agrometeorological-indicators",
                "license_name": "CC-BY licence",
                "license_url": "https://spdx.org/licenses/CC-BY-4.0",
                "citation": "Copernicus Climate Change Service (C3S): AgERA5 agrometeorological indicators",
                "retention_days": None,
                "reviewed_at": "2026-08-08T00:00:00Z",
                "reviewed_by": "local-data-operator",
            },
            "window": {
                "start_date": "2022-04-30",
                "end_date": "2026-04-30",
            },
            "dataset": AGERA5_DATASET_ID,
            "version": AGERA5_DATASET_VERSION,
            "requested_grid_degrees": 0.1,
            "cells": [
                {
                    "cell_key": "sentinel2-ndvi-0p25deg:43.1250:-116.3750",
                    "latitude": 43.125,
                    "longitude": -116.375,
                }
            ],
            "parameters": sorted(AGERA5_SIGNAL_SPECIFICATIONS.keys()),
            "transform_version": "cds-agera5-normalization-v1",
            "release_set_key": "cds-agera5-pnw-20220430-20260430",
            "release_set_as_of": "2026-08-08T23:59:59Z",
            "description": "AgERA5 agrometeorological indicators over PNW test lattice",
        }
    )


def main() -> None:
    plan = build_agera5_plan()
    PLAN_OUTPUT_PATH.write_bytes(canonical_json_bytes(plan.model_dump(mode="json")))
    print(f"Authored AgERA5 plan to {PLAN_OUTPUT_PATH} (checksum: {plan.plan_checksum})")


if __name__ == "__main__":
    main()
