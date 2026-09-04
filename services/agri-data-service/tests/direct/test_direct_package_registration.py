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

# Packages under `pipeline/direct/` whose source-direct writer is built but still not routed to by
# LANE_REGISTRY's own adapter. All three survived the 2026-09-04 join: each writer got its own
# EXECUTOR lane (`execution/job_executor_service.py`), but every one of those lanes ships SHADOW, and
# a shadow writer cannot be the registration's adapter while the generic `parquet-*` lane beside it
# is the one production actually runs. Each entry cites its OWN reason -- these are three different
# reasons, not one shared "owed at the join step".
#
# DEFAULT-DENY, the same convention as `test_lane_registry.py::UNREGISTERED_LANE_MODULES`: a package
# added later is policed the day it lands, with nothing to remember to register.
PENDING_REGISTRATION: dict[str, str] = {
    "vegetation": "environmental_postgres_retirement_20260904 F-B3/join: writer built "
    "(pipeline/direct/vegetation/) and its executor lane IS registered "
    "(vegetation-sentinel2-ndvi-direct-forward, execution/job_executor_service.py), but "
    "LANE_REGISTRY['vegetation'].adapter deliberately still reads Postgres via _fill_vegetation. "
    "pipeline/direct/vegetation/backfill.py:149-155 depends on that exact, unchanged adapter to reach "
    "D2 parity for every day at or before VEGETATION_DIRECT_WRITER_START_DAY; routing the registration "
    "to a source-direct refusal before that backfill discharges would make "
    "adapter.py::refuse_pre_ownership_day reject the entire backfill window by construction. Remove "
    "this entry only once backfill.py reports the window closed.",
    "weather_observations": "environmental_postgres_retirement_20260904 F-B3/join: writer built "
    "(pipeline/direct/weather_observations/) and its executor lane IS registered "
    "(weather-observations-direct-forward, execution/job_executor_service.py), but "
    "LANE_REGISTRY['weather-observations'].adapter deliberately still reads Postgres via "
    "_fill_weather_observations. Unlike vegetation/fire-detections/water-gauges, this package ships no "
    "*_DIRECT_WRITER_START_DAY-equivalent constant and no backfill.py -- "
    "pipeline/direct/weather_observations/__init__.py only says the registry 'may come to import a "
    "submodule ... for its floor and lag', not that one exists yet -- and parity.py still frames "
    "Postgres as the ground list D2 must cover. Routing the registration to a refusal without a cited "
    "ownership-boundary day would risk the same silent wedge vegetation's backfill.py warns about, for "
    "a lane with no backfill.py to even raise the alarm. Remove this entry once that boundary is "
    "measured and cited.",
    "drought": "environmental_postgres_retirement_20260904 F-B3/join: writer built "
    "(pipeline/direct/drought/) and its executor lane IS registered (drought-direct-forward, "
    "execution/job_executor_service.py), but that lane is SHADOW -- it runs only once an owner adds it "
    "to PLANTGEO_JOB_EXECUTOR_ACTIVE_LANES -- while parquet-drought is ACTIVE in production and is the "
    "only writer this layer has. Routing LANE_REGISTRY['drought'].adapter to a source-direct refusal "
    "before that activation gives the layer NO writer at all: gap_fill._export_one_day catches the "
    "LaneRegistryError as outcome 'raised' (a FAILING_LANE_OUTCOMES member) every tick, forever. "
    "Unlike vegetation there is also no boundary day to abut -- forward.py and backfill.py claim the "
    "same full floor-to-settled window the generic lane covers -- so no writer_ceiling can bridge the "
    "gap either. Remove this entry in the SAME push that activates drought-direct-forward.",
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
