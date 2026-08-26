"""Read-only vegetation operator scripts keep their proof and exit contracts."""

from __future__ import annotations

import importlib.util
import sys
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

import pyarrow as pa  # type: ignore[import-untyped]
import pytest

from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.pipeline.parquet.vegetation_rewrite import LEGACY_VEGETATION_BASE_SCHEMA
from agri_data_service.warehouse.schemas.vegetation import VEGETATION_PLANE_SCHEMA
from tests.parquet.test_objectstore_writer import RecordingBackend

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from types import ModuleType

_SERVICE_ROOT = Path(__file__).parents[1]
EXPECTED_CHANGED_CELL_DAY_COUNT = 7


def _load_script(name: str) -> ModuleType:
    path = _SERVICE_ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_inventory_distinguishes_exact_legacy_from_unknown_schema() -> None:
    inventory = _load_script("vegetation_source_inventory")
    unknown = VEGETATION_PLANE_SCHEMA.arrow_schema.append(pa.field("unexpected", pa.string()))

    assert inventory._schema_label(VEGETATION_PLANE_SCHEMA.arrow_schema) == "current"
    assert inventory._schema_label(LEGACY_VEGETATION_BASE_SCHEMA) == "exact_legacy"
    assert inventory._schema_label(unknown) == "unknown"


@pytest.mark.asyncio
async def test_inventory_fails_clean_flag_when_source_roster_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    inventory = _load_script("vegetation_source_inventory")
    calls = 0

    class Session:
        async def execute(self, *_args: object, **_kwargs: object) -> None:
            return None

        async def rollback(self) -> None:
            return None

    @asynccontextmanager
    async def sessions(_database_url: str) -> AsyncIterator[Session]:
        yield Session()

    async def cells(_session: object) -> tuple[UUID, ...]:
        nonlocal calls
        calls += 1
        suffix = 1 if calls == 1 else 2
        return (UUID(f"00000000-0000-4000-8000-{suffix:012d}"),)

    async def no_source(*_args: object, **_kwargs: object) -> tuple[object, ...]:
        return ()

    class Stores:
        @staticmethod
        def from_settings() -> ObjectStore:
            return ObjectStore(RecordingBackend())

    class Settings:
        @staticmethod
        def require_local_source_loader_database_url() -> str:
            return "postgresql://test"

    monkeypatch.setattr(inventory, "ObjectStore", Stores)
    monkeypatch.setattr(inventory, "settings", Settings())
    monkeypatch.setattr(inventory, "local_source_loader_session", sessions)
    monkeypatch.setattr(inventory, "spatial_cell_ids", cells)
    monkeypatch.setattr(inventory, "fetch_source_cell_days", no_source)

    report = await inventory.inventory(date(2026, 8, 19), date(2026, 8, 19), date(2026, 8, 19))

    assert report["source_changed_during_inventory"] is True
    assert report["clean"] is False


def test_inventory_main_exits_nonzero_for_an_unsafe_report(monkeypatch: pytest.MonkeyPatch) -> None:
    inventory = _load_script("vegetation_source_inventory")

    async def unsafe(*_args: object, **_kwargs: object) -> dict[str, bool]:
        return {"clean": False}

    monkeypatch.setattr(inventory, "inventory", unsafe)
    monkeypatch.setattr(sys, "argv", ["vegetation_source_inventory.py"])

    with pytest.raises(SystemExit) as error:
        inventory.main()

    assert error.value.code == 1


def test_ingest_status_payload_is_empty_safe_and_reports_promotion_target() -> None:
    status = _load_script("vegetation_ingest_status")
    since = datetime(2026, 8, 25, tzinfo=UTC)
    payload = status._status_payload(
        {
            "cells": 0,
            "changed_cell_days_not_governed": EXPECTED_CHANGED_CELL_DAY_COUNT,
            "changed_cell_days_since": EXPECTED_CHANGED_CELL_DAY_COUNT,
            "first_day": None,
            "invalid_observed_day_rows": 0,
            "last_day": None,
            "newest_created_at": None,
            "newest_updated_at": None,
            "raw_rows": 0,
            "rows_changed_since": EXPECTED_CHANGED_CELL_DAY_COUNT,
        },
        since,
    )

    assert payload["first_day"] is None
    assert payload["newest_created_at"] is None
    assert payload["changed_cell_days_since"] == EXPECTED_CHANGED_CELL_DAY_COUNT
    assert payload["changed_cell_days_not_governed"] == EXPECTED_CHANGED_CELL_DAY_COUNT
