"""Every `pipeline/direct/**` package is either registered or named pending, never neither.

Sibling to `test_lane_registry.py`'s
`test_every_lane_module_is_either_registered_or_explicitly_declared_unregistered`, one level up:
that test polices `pipeline/lanes/*.py` (the OLD Postgres-reading adapters); this one polices the
NEW source-direct writer packages replacing them.
Today nothing does this: `test_lane_registry.py` never walks `pipeline/direct/**`, and the per-package
tests (`tests/direct/climate/test_lane_registrations.py`, `tests/direct/soil/test_lane_registrations.py`)
only assert facts about the packages they already know are wired -- neither generalizes to a package
nobody has registered yet, so three unregistered packages (`vegetation`, `weather_observations`,
`drought`) correctly pass every existing test today.
"""

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

#: A day cheap to probe with -- never actually read or written, since every adapter below is called
#: with a null session and store.
PROBE_DAY = date(2026, 8, 6)

# Packages under `pipeline/direct/` whose source-direct writer is built but not yet routed to by
# LANE_REGISTRY -- each stream is still owned by the OLD Postgres-reading adapter in `pipeline/lanes/`
# (`_fill_vegetation`, `_fill_weather_observations`, `_fill_drought`,
# `pipeline/parquet/lane_registry.py`). Naively checking "is the package name a registered slug"
# would wrongly call all three registered today, since `vegetation`, `weather-observations` and
# `drought` are already registered slugs -- just not routed to THESE packages yet.
#
# DEFAULT-DENY, the same convention as `test_lane_registry.py::UNREGISTERED_LANE_MODULES`: a package
# added later is policed the day it lands, with nothing to remember to register.
PENDING_REGISTRATION: dict[str, str] = {
    "vegetation": "environmental_postgres_retirement_20260904 F-B3: writer built "
    "(pipeline/direct/vegetation/), not yet routed to by LANE_REGISTRY -- owed at the join step.",
    "weather_observations": "environmental_postgres_retirement_20260904 F-B3: writer built "
    "(pipeline/direct/weather_observations/), not yet routed to by LANE_REGISTRY -- owed at the join step.",
    "drought": "environmental_postgres_retirement_20260904 F-B3: writer built "
    "(pipeline/direct/drought/), not yet routed to by LANE_REGISTRY -- owed at the join step.",
}


async def _refused_writer_message(registration: LaneRegistration) -> str | None:
    """Call one registration's adapter with a null session/store; return the `LaneRegistryError`
    message it raises, or None when the adapter is not (or not yet) a source-direct refusal.

    Safe to call on EVERY registration, not only the ones already known to be source-direct: a real
    Postgres adapter given `session=None, store=None` fails on that attribute access before it ever
    reads or writes anything -- the identical probe
    `test_every_source_direct_lane_refuses_and_names_its_own_writer`
    (`tests/parquet/test_lane_registry.py`) already performs against the known source-direct slugs.
    This performs it against every slug, because the whole point here is not knowing in advance
    which slug (if any) has been routed to a given `pipeline/direct/**` package.
    """
    try:
        await registration.adapter(None, None, day=PROBE_DAY, run_id="registration-probe")
    except LaneRegistryError as error:
        return str(error)
    except Exception:
        return None
    return None


@pytest.mark.asyncio
async def test_every_direct_package_is_either_registered_or_named_pending() -> None:
    """A `pipeline/direct/**` package nothing registers is a writer nothing gap-fills notices is missing.

    "Registered" means: some LANE_REGISTRY adapter, called with a null session/store, raises
    `LaneRegistryError` naming `pipeline.direct.<package>` as the writer that owns it -- the exact
    `_source_direct_refusal` signal `climate` and `soil` already carry
    (`pipeline/parquet/lane_registry.py`). Removing a package from PENDING_REGISTRATION without
    actually wiring that refusal (or an equivalent registered writer) makes it "unaccounted" below
    and fails this test.
    """
    for package, reason in PENDING_REGISTRATION.items():
        assert reason.strip(), package

    packages = {
        path.name for path in DIRECT_PACKAGE_DIRECTORY.iterdir() if path.is_dir() and (path / "__init__.py").is_file()
    }
    probed = [await _refused_writer_message(registration) for registration in LANE_REGISTRATIONS]
    messages = [message for message in probed if message is not None]
    registered = {
        package for package in packages if any(f"pipeline.direct.{package}" in message for message in messages)
    }
    exempt = set(PENDING_REGISTRATION)

    assert exempt <= packages, f"an exemption names a direct package that no longer exists: {sorted(exempt - packages)}"
    assert not (registered & exempt), "a package cannot be both registered and pending"
    unaccounted = sorted(packages - registered - exempt)
    assert not unaccounted, (
        f"direct package(s) {unaccounted} have no LANE_REGISTRY adapter that refuses and names "
        "`pipeline.direct.<package>` as its writer, and no declared exemption. Either wire its writer "
        "into pipeline/parquet/lane_registry.py or record why it is still pending in "
        "PENDING_REGISTRATION above."
    )
