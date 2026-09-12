"""Every source-direct package is registered with an explicit refusal adapter."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from agri_data_service.pipeline.parquet.lane_registry import LANE_REGISTRATIONS, LaneRegistryError

if TYPE_CHECKING:
    from agri_data_service.pipeline.parquet.lane_registry import LaneRegistration

_SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src" / "agri_data_service"
DIRECT_PACKAGE_DIRECTORY = _SOURCE_ROOT / "pipeline" / "direct"
PROBE_DAY = date(2026, 8, 6)


async def _refused_writer_message(registration: LaneRegistration) -> str | None:
    """Probe an adapter without a session or object store and return its refusal text."""
    try:
        await registration.adapter(None, None, day=PROBE_DAY, run_id="registration-probe")
    except LaneRegistryError as error:
        return str(error)
    except Exception:
        return None
    return None


@pytest.mark.asyncio
async def test_every_direct_package_is_registered_without_a_postgres_fallback() -> None:
    packages = {
        path.name
        for path in DIRECT_PACKAGE_DIRECTORY.iterdir()
        if path.is_dir() and (path / "__init__.py").is_file()
    }
    messages = [
        message
        for message in [await _refused_writer_message(registration) for registration in LANE_REGISTRATIONS]
        if message is not None
    ]
    registered = {
        package
        for package in packages
        if any(f"pipeline.direct.{package}" in message for message in messages)
    }

    assert registered == packages
