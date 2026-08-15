"""Author CEMS fire danger index plan for EWDS host.

Usage:
    python plans/author_cems_plans.py
"""

from __future__ import annotations

import sys
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVICE_ROOT / "src"))

from agri_data_service.execution.contracts import canonical_json_bytes  # noqa: E402
from agri_data_service.execution.historical_cems import (  # noqa: E402
    CEMS_DATASET_ID,
    CEMS_FIRE_SIGNAL_SPECIFICATIONS,
    HistoricalCemsBackfillPlan,
)

PLAN_OUTPUT_PATH = SERVICE_ROOT / "plans" / "ewds-cems-pnw-20220430-20260430.json"


def build_cems_plan() -> HistoricalCemsBackfillPlan:
    return HistoricalCemsBackfillPlan.model_validate(
        {
            "source": {
                "key": "copernicus-ewds-cems-fire",
                "name": "CEMS Fire Danger Indices (ECMWF GEFF model reanalysis)",
                "owner": "Copernicus Emergency Management Service",
                "purpose": "Pacific Northwest fire danger indices baseline",
                "base_url": "https://ewds.climate.copernicus.eu/datasets/cems-fire-historical-v1",
                "license_name": "Copernicus Licence",
                "license_url": "https://spdx.org/licenses/CC-BY-4.0",
                "citation": "Copernicus Emergency Management Service (CEMS): GEFF fire danger indices",
                "retention_days": None,
                "reviewed_at": "2026-08-08T00:00:00Z",
                "reviewed_by": "local-data-operator",
            },
            "window": {
                "start_date": "2022-04-30",
                "end_date": "2026-04-30",
            },
            "dataset": CEMS_DATASET_ID,
            "product_type": "reanalysis",
            "system_version": "4_1",
            "requested_grid_degrees": 0.25,
            "cells": [
                {
                    "cell_key": "sentinel2-ndvi-0p25deg:43.1250:-116.3750",
                    "latitude": 43.125,
                    "longitude": -116.375,
                }
            ],
            "parameters": sorted(CEMS_FIRE_SIGNAL_SPECIFICATIONS.keys()),
            "transform_version": "cems-fire-normalization-v1",
            "release_set_key": "ewds-cems-pnw-20220430-20260430",
            "release_set_as_of": "2026-08-08T23:59:59Z",
            "description": "CEMS Fire Danger Indices over PNW test lattice",
        }
    )


def main() -> None:
    plan = build_cems_plan()
    PLAN_OUTPUT_PATH.write_bytes(canonical_json_bytes(plan.model_dump(mode="json")))
    print(f"Authored CEMS plan to {PLAN_OUTPUT_PATH} (checksum: {plan.plan_checksum})")


if __name__ == "__main__":
    main()
