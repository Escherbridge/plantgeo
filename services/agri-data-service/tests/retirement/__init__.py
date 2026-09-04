"""Tests for the drop-packet tooling (`src/agri_data_service/retirement/`).

Everything here runs against synthetic checkouts under `tmp_path` and synthetic receipts. That is not
a convenience: production was unreachable when the tooling was written, and a packet builder whose
refusals could only be exercised against a live database would be untestable exactly when a refusal
matters most.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

#: A minimal inventory carrying one row of each class, in the real file's column shape.
SYNTHETIC_INVENTORY: Final = """---
type: evidence
---

# Retirement inventory

## Drop now

| relation | schema | filled by | read by | classification | gating layer | notes |
|---|---|---|---|---|---|---|
| `mv_orphan` | `geo` | NONE | NONE | drop now | n/a | nothing reads it |
| `spatial_cell` | `agri` | writer | reader | drop now | n/a | the correction case |
| `historical_a`, `historical_b` | `geo` | NONE | validation | drop now, with one caveat | n/a | two in one cell |

## Drop after Parquet proof

| relation | schema | filled by | read by | classification | gating layer | notes |
|---|---|---|---|---|---|---|
| `features` | `geo` | seven commands | the app | drop after Parquet proof | all seven | polymorphic |
| `drought_areas` | `geo` | drought-ingestion | the app | drop after Parquet proof | drought | own table |
| `mv_signal_observation_day` | `geo` | refresher | none | drop after Parquet proof | signal | the dependent case |
| `watershed_rollup` | `geo` | refresher | tiles | drop after Parquet proof | watersheds | matview, no mv_ prefix |
| `v_thing` | `geo` | n/a | app | drop after Parquet proof | n/a | a plain view |
| `mystery` | `geo` | n/a | n/a | pondered gently | n/a | an unmappable class |

## Keep

| relation | schema | filled by | read by | classification | gating layer | notes |
|---|---|---|---|---|---|---|
| `job_run` | `agri` | executor | everything | keep | n/a | the executor ledger |
"""

#: The path the ledger reads the inventory from, relative to a checkout root.
INVENTORY_PATH: Final = "conductor/tracks/environmental_postgres_retirement_20260904/evidence/retirement-inventory.md"


def build_checkout(root: Path, *, files: Mapping[str, str] | None = None, inventory: str = SYNTHETIC_INVENTORY) -> Path:
    """Create a minimal repository the scanner accepts, plus any surface files a test needs.

    The three root markers (`drizzle`, `services/agri-data-service`, `src`) exist so
    `find_repository_root` would accept this tree; every test still passes the root explicitly, so a
    scan can never escape into the real checkout.
    """
    for marker in ("drizzle", "services/agri-data-service", "src"):
        (root / marker).mkdir(parents=True, exist_ok=True)
    inventory_path = root / INVENTORY_PATH
    inventory_path.parent.mkdir(parents=True, exist_ok=True)
    inventory_path.write_text(inventory, encoding="utf-8")
    for relative, content in (files or {}).items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root


def clean_drought_receipt(*, days: int = 209, rows: int = 41_800) -> dict[str, object]:
    """Return a drought parity receipt that reports full coverage."""
    return {
        "postgres_days": days,
        "postgres_rows": rows,
        "parquet_days": days,
        "parquet_rows": rows,
        "parquet_incomplete_days": [],
        "missing_from_parquet": [],
        "row_count_mismatches": [],
        "parity_achieved": True,
    }


def short_drought_receipt() -> dict[str, object]:
    """Return a drought parity receipt that reports a genuine shortfall."""
    receipt = clean_drought_receipt()
    receipt["missing_from_parquet"] = ["2026-08-25", "2026-09-01"]
    receipt["parity_achieved"] = False
    return receipt


def instant(day: int) -> datetime:
    """Return an aware instant in September 2026, for epoch comparisons."""
    return datetime(2026, 9, day, tzinfo=UTC)
