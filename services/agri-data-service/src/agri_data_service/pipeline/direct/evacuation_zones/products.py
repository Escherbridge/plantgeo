"""The evacuation-zones lane's identity and its Oregon-only coverage gate.

Holds the constants `source.py`, `rows.py`, `adapter.py`, `watermark.py`, `forward.py` and
`parity.py` all need, in one place a test can import without pulling the driver -- the same reason
`drought/products.py`, `climate/products.py` and `soil/products.py` exist.

DELIBERATELY CARRIES NO EDGE TO `pipeline/parquet/lane_registry.py`. The registry imports
`watermark.py` (the body of the registered `_evacuation_zones_watermark` resolver), and that module
reaches this one through `rows.py`; an import of `LANE_REGISTRY` here would close that chain into a
cycle back through a half-initialised registry. `evacuation_zones_lane_registration()` therefore
lives in `registration.py`, which nothing else in this package imports.
"""

from __future__ import annotations

from typing import Final

from agri_data_service.ingest.evacuation_zones import (
    EVACUATION_ZONES_PRODUCER,
    EVACUATION_ZONES_PROPERTY_SOURCE,
    EVACUATION_ZONES_QUERY_URL,
)
from agri_data_service.ingest.policy import UNCONFIGURED_BBOX_REASON, resolve_bounded_bbox
from agri_data_service.warehouse.schemas.evacuation_zones import EVACUATION_ZONES_STREAM

#: `Final` so the value narrows to the `PartitionKind` literal rather than to bare `str`. There is no
#: `kind=forecast` sibling: an evacuation level is a policy decision by an emergency manager, not a
#: physical process to project (`docs/lanes/evacuation-zones.md` section 7, `horizon: none`).
EVACUATION_ZONES_DIRECT_KIND: Final = "observed"

#: Restated rather than imported from `pipeline/lanes/evacuation_zones.py:36`, which this package
#: replaced and WHICH WAS DELETED ON 2026-09-06: importing a constant out of a module being removed
#: is how a "just delete the old lane" push turns into a broken import, and this restatement is what
#: made that deletion a no-op here. The number and its reasoning are unchanged -- this layer has
#: never been censused for polygon byte size
#: (`docs/lanes/evacuation-zones.md` section 5), and the closest measured precedent (burn-severity)
#: hid 37.5 MB in 541 rows, so one part file is bounded by row count until a real byte census exists.
MAX_ROWS_PER_PART: Final = 200

#: The only jurisdiction this lane covers. RESTATED, not imported: `planes/evacuation_zones.py:58`
#: holds the serving-side copy (`_COVERED_STATE`) and this package may not import `planes` -- a
#: plane may import `pipeline`, never the reverse (`planes/evacuation_zones.py` module docstring).
#: `tests/direct/test_evacuation_zones_direct_products.py` pins the two spellings together so the
#: writer's population and the serving layer's coverage claim can never drift apart silently.
#:
#: The coverage is structural, not a filter: Oregon OEM's `Fire_Evacuation_Areas_Public` is a single
#: state agency's feed, and no equivalent government-run aggregator exists for Washington, Idaho or
#: western Montana (`ingest/evacuation_zones.py:68-72`, `docs/lanes/evacuation-zones.md` section 5).
#: There is no per-row state flag to widen or narrow, so a writer widens coverage only by fetching a
#: DIFFERENT endpoint -- which is why `source.py` imports `EVACUATION_ZONES_QUERY_URL` and accepts no
#: caller-supplied URL.
COVERED_STATE: Final = "oregon"

#: Re-exported so a caller of this package never has to reach into `ingest/` for the three facts that
#: define what "this lane" means: which producer mints its keys, which upstream endpoint it reads,
#: and the constant `source` literal every stored row carries.
DIRECT_PRODUCER: Final = EVACUATION_ZONES_PRODUCER
DIRECT_SOURCE_LITERAL: Final = EVACUATION_ZONES_PROPERTY_SOURCE
DIRECT_QUERY_URL: Final = EVACUATION_ZONES_QUERY_URL


class EvacuationZonesCoverageError(RuntimeError):
    """Raised when a turn is asked to publish outside this lane's Oregon-only coverage."""


def resolve_coverage_bbox(override: str | None = None) -> str | None:
    """Return the policy-checked ingestion bbox, or None when neither an override nor INGEST_BBOX is set.

    THE UNSET-BBOX SKIP IS THE GATE, AND IT IS PRESERVED VERBATIM.
    `run_evacuation_zones_ingestion_job` (`ingest/evacuation_zones.py:430-432`) returns a
    `skipped_result` rather than failing when no bbox is configured, pinned by
    `tests/test_ingest_evacuation_zones.py:351-355`. A direct writer that defaulted to a bbox of its
    own -- or to Oregon's full extent, or to no envelope at all -- would query a wider population
    than the Postgres lane ever did while every downstream consumer still believed the coverage
    contract in `docs/lanes/evacuation-zones.md` section 4: "bounded twice, once by Oregon's own
    statewide feed, and again by whatever INGEST_BBOX the run is configured with".

    Delegates to `ingest/policy.py::resolve_bounded_bbox` rather than re-deriving the policy check,
    so the 30-degree longitude / 20-degree latitude span ceiling and the `west,south,east,north`
    parse are the SAME code the ingestion job was gated by.
    """
    return resolve_bounded_bbox(override)


def bbox_unconfigured_reason() -> str:
    """The exact reason string the ingestion job reported for an unset bbox, reused unchanged."""
    return UNCONFIGURED_BBOX_REASON


def refuse_uncovered_state(state: str) -> None:
    """Refuse a turn asked to publish for any state other than Oregon.

    Not a filter over rows -- there is nothing per-row to filter (see `COVERED_STATE`) -- but a
    guard on the one caller-supplied string that could ever imply this lane speaks for somewhere
    else. A writer that silently widened coverage would put Washington on a map as "no active
    evacuation zones" when the truth is "PlantGeo has no feed here at all", which
    `planes/evacuation_zones.py`'s four-way answer status exists specifically to keep apart.
    """
    if state.strip().casefold() != COVERED_STATE:
        raise EvacuationZonesCoverageError(
            f"{state!r} is outside evacuation-zones' Oregon-only coverage: Oregon OEM's "
            "Fire_Evacuation_Areas_Public is a single state agency's feed and no equivalent "
            "government-run aggregator exists for Washington, Idaho or western Montana "
            "(docs/lanes/evacuation-zones.md section 5). Publishing under another state's name "
            "would report a structural absence of coverage as a quiet, covered jurisdiction"
        )


__all__ = [
    "COVERED_STATE",
    "DIRECT_PRODUCER",
    "DIRECT_QUERY_URL",
    "DIRECT_SOURCE_LITERAL",
    "EVACUATION_ZONES_DIRECT_KIND",
    "EVACUATION_ZONES_STREAM",
    "MAX_ROWS_PER_PART",
    "EvacuationZonesCoverageError",
    "bbox_unconfigured_reason",
    "refuse_uncovered_state",
    "resolve_coverage_bbox",
]
