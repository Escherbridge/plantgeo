"""Every `pipeline/direct/**` package is either registered or named pending, never neither.

Sibling to `test_lane_registry.py`'s
`test_every_lane_module_is_either_registered_or_explicitly_declared_unregistered`, one level up:
that test polices `pipeline/lanes/*.py` (the OLD Postgres-reading adapters); this one polices the
NEW source-direct writer packages replacing them.
Today nothing does this: `test_lane_registry.py` never walks `pipeline/direct/**`, and the per-package
tests (`tests/direct/climate/test_lane_registrations.py`, `tests/direct/soil/test_lane_registrations.py`)
only assert facts about the packages they already know are wired -- neither generalizes to a package
nobody has registered yet, so the unregistered packages (three after the 2026-09-04 join, eight after
the 2026-09-06 wave-B join, six once watersheds and evacuation-zones were swapped later that day, TWO
once burn-severity, drought, sensors and weather-observations were swapped on 2026-09-07) correctly
pass every existing test today.
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
# LANE_REGISTRY's own adapter. TWO are left, and they are left for two DIFFERENT reasons -- neither of
# which is "the join has not got to it yet". The four entries removed on 2026-09-07 each recorded a
# condition ("in the same push that activates the direct lane", "once the owner stops mtbs-forward",
# "while geo.features is the only path to older days", "once a boundary day is measured"); each of
# those conditions was discharged before its entry came out. Each surviving entry must cite its OWN
# reason -- an entry that could be swapped for any other entry's text is an entry that has stopped
# saying anything.
#
# THE GENERAL RULE THE FOUR REMOVALS TURNED ON: a shadow writer cannot be the registration's adapter
# while the generic `parquet-*` lane beside it is the one production actually runs. Read the other way
# -- which is how the four were cleared -- once the direct lane is ACTIVE and the generic lane retired,
# a Postgres-reading adapter is not a fallback, it is a re-export of a frozen table waiting for someone
# to re-activate a lane.
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
    "fire_perimeters": "environmental_postgres_retirement_20260904 wave-B/join 2026-09-06: writer built "
    "(pipeline/direct/fire_perimeters/) and its executor lane IS registered (fire-perimeters-direct-forward, "
    "execution/job_executor_service.py), shadow. This lane owes TWO substitutions, not one: "
    "LANE_REGISTRY['fire-perimeters'].adapter (_fill_fire_perimeters) AND its watermark "
    "(_fire_perimeters_watermark) both read geo.features, and forward.py substitutes both at runtime "
    "before calling fill_one_lane_day -- so the registered pair is the fallback the GENERIC gap-fill "
    "lane still runs on. No writer_ceiling can straddle the handover either: this is a static_lookup, "
    "and LaneRegistration.__post_init__ refuses a ceiling on a version-stamped lane outright. Nor is "
    "there a backfill to bound one against, and there cannot be: WFIGS _Current does not retain what it "
    "reported yesterday, so no past version is re-fetchable. Remove this entry in the push that drops "
    "geo.features for this layer, replacing adapter and watermark together.",
    # `watersheds` and `evacuation_zones` were HERE until 2026-09-06 and are now REGISTERED, adapter
    # and watermark swapped in one edit each -- see `tests/parquet/test_lane_registry.py`'s
    # STATIC_SOURCE_DIRECT_SLUGS. Their entries said the two fields could not be sequenced, and that
    # is exactly how they moved; the two Postgres watermark query files were deleted in the same edit.
    #
    # `burn_severity`, `drought`, `sensors` and `weather_observations` were HERE until 2026-09-07 and
    # are now REGISTERED -- see that file's SERIES_SOURCE_DIRECT_SLUGS. Their four executor lanes are
    # ACTIVE and the `parquet-*` lanes they conflict with are retired, so every "the generic lane is
    # the writer this stream actually has" clause those entries turned on had stopped being true.
    # `pipeline/lanes/drought.py` and `pipeline/lanes/sensors.py` were deleted with them, along with
    # the three query files only they loaded; burn-severity's and weather-observations' day-export SQL
    # SURVIVES, because `pipeline/validation/burn_severity.py` and
    # `pipeline/validation/weather_observations_exact.py` still read those tables for parity.
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
