"""The report's vocabulary: the axis constants mirrored from the TypeScript read model, the scan bounds, and
the validity checks with the one line each says about what it breaks downstream."""

from __future__ import annotations

import re
from decimal import Decimal
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Literal

from agri_data_service.ingest.archive_walk import archive_lane_definition_name
from agri_data_service.ingest.identity import MAX_NATURAL_KEY_LENGTH, PRODUCER_BY_LAYER_NAME
from agri_data_service.ingest.lanes import BACKFILL_LANES

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import date

# ---------------------------------------------------------------------------------------------------------------
# Constants mirrored from the TypeScript read model. See ingest/AGENTS.md "validation.py".
#
# THE COUPLING IS THE POINT: this report exists to answer "what will the user actually be able to scrub
# through", and it can only answer that if it applies the SAME two axis rules the slider applies. A report
# that used a bare MIN(observed_day) would call water-gauges complete back to 1990 while the slider starts it
# at 2026-08. Every value below is copied from
# `src/lib/server/services/environmental-read-model.ts`; the line numbers are where each one lives today.
#
# THE COUPLING IS ENFORCED, not trusted: `tests/test_ingest_validation.py` reads the two axis constants back
# out of that TypeScript file and asserts they equal the mirrors here, so a change on either side fails the
# Python suite loudly. Line numbers drift, so the test greps the declarations by name rather than by line.
# ---------------------------------------------------------------------------------------------------------------

# Repository-root-relative, which is what makes it readable from both the Python suite and a report reader.
MIRRORED_READ_MODEL_PATH: Final = "src/lib/server/services/environmental-read-model.ts"

# environmental-read-model.ts:1961 `OBSERVATION_CLUSTER_GAP_DAYS`. Gap, in days, that ends a layer's
# continuous record. 21, NOT 45: measured against production the largest gap inside water-gauges' real
# record is 15 days while the gap back to its 1990s stragglers is 23, and fire-perimeters' largest real gap
# is 12 against 324 back to its isolated 2025-07-28 row. Widening this to 45 would swallow the 23-day
# straggler gap and hand the slider a 36-year axis again.
OBSERVATION_CLUSTER_GAP_DAYS: Final = 21

# environmental-read-model.ts:1998 `OBSERVATION_DENSITY_FLOOR_FRACTION`. A day must carry at least this
# fraction of the busiest day in the newest cluster to anchor the start of the axis. 1% sits inside the
# measured band (0.064%, 5.26%] that holds for every layer at once.
OBSERVATION_DENSITY_FLOOR_FRACTION: Final = Decimal("0.01")

# Postgres evaluates `CEIL(MAX(observation_count) * 0.01::numeric)` in exact numeric arithmetic. Doing the
# same multiplication in binary floating point disagrees: 300 * 0.01 is 3.0000000000000004 as a double, so
# math.ceil answers 4 where Postgres answers 3, and the report would exclude a day the slider keeps.
# Decimal reproduces the numeric result exactly, which is why the fraction above is a Decimal.
MINIMUM_DENSITY_FLOOR: Final = 1

# environmental-read-model.ts:117 `USGS_NO_DATA_SENTINEL`. USGS NWIS writes this in place of a reading it
# does not have. Compared EXACTLY, never as "negative means missing": genuine reverse flow is recorded at
# these gauges down to -172,000 cfs.
USGS_NO_DATA_SENTINEL: Final = -999999.0

# environmental-read-model.ts:2290-2292, the `observed` CTE of readObservationWindows. The axis counts only
# published rows that are linked to geo.geometry; an unlinked row is drawn on the map but is invisible to the
# time axis, so counting it here would advertise a day the slider cannot serve.
PUBLISHED_FEATURE_STATUS: Final = "published"

# environmental-read-model.ts:2168 `METRIC_SOURCES["streamflow-cfs"].missingValueSentinel`, which names the
# one property the sentinel is stored in. Only water-gauges has an upstream "no reading" marker that is a
# real number rather than an absent key.
MISSING_VALUE_SENTINEL_PROPERTY_BY_STREAM: Final[Mapping[str, str]] = MappingProxyType({"water-gauges": "flowCfs"})


def producer_local_id_ceiling(producer: str) -> int:
    """The longest stored `properties->>'id'` identity.py can still namespace inside its 255-character key."""
    # writer.py:66-68 stores `identity.producer_local_id`, NOT `identity.natural_key` -- the producer prefix is
    # deliberately absent from the stored id and lives only on the geometry dimension's key. So the ceiling the
    # warehouse must respect is the natural key's 255 minus the producer token and its colon. Checking for the
    # prefix instead would flag every producer-backed row in production as malformed; measured 2026-08-08,
    # that mistake reported 476,016 of 476,016 fire-detections rows as broken.
    return MAX_NATURAL_KEY_LENGTH - len(producer) - 1


PRODUCER_LOCAL_ID_CEILING_BY_STREAM: Final[Mapping[str, int]] = MappingProxyType(
    {stream: producer_local_id_ceiling(producer) for stream, producer in PRODUCER_BY_LAYER_NAME.items()}
)


def _day_text(day: date | None) -> str | None:
    """Render an optional calendar day as an ISO string."""
    # Lives beside the vocabulary rather than with the models because BOTH renderers reach for it, and putting
    # it on either one would make the models and the Markdown module import each other.
    return None if day is None else day.isoformat()


# ---------------------------------------------------------------------------------------------------------------
# Scan bounds. Every statement in `queries.py` is read-only, holds no lock beyond the snapshot, and runs inside
# a transaction pinned READ ONLY with a 120-second statement timeout (the repo's direct-SQL convention).
# ---------------------------------------------------------------------------------------------------------------

# The full-scan convention for direct SQL in this service; see ingest/AGENTS.md.
STATEMENT_TIMEOUT_SECONDS: Final = 120

# The day series is one row per (stream, observed day). Production holds ~2,200 such rows across eleven
# layers and fourteen years. 200,000 is two orders of magnitude of headroom and still small enough to hold in
# memory. Hitting it is REFUSED rather than truncated: a truncated day series silently invents gaps at the
# tail, which is exactly the defect this report exists to find.
MAX_OBSERVED_DAY_ROWS: Final = 200_000

# One row per (lane, run, work-item status). Eight statuses times any plausible lane-and-run count; a lane
# mints one extra run each time its floor is lowered, which is a handful over the service's whole life.
MAX_LANE_STATE_ROWS: Final = 1_000

# The worst gaps and the thinnest days are listed explicitly; the rest are counted. Never silently truncated
# -- both caps report how many entries they omitted.
MAX_REPORTED_GAPS: Final = 10
MAX_REPORTED_THIN_DAYS: Final = 10

# The grid a stream that declares no cadence is walked on, and the one cadence at which "a missing publication"
# and "a missing calendar day" are the same thing.
DAILY_PUBLICATION_CADENCE_DAYS: Final = 1

_ISO_DAY_IN_SHARD_KEY: Final = re.compile(r"\d{4}-\d{2}-\d{2}")

# A work item in one of these states is settled; anything else is a window the lane still owes.
SETTLED_WORK_ITEM_STATES: Final[frozenset[str]] = frozenset({"succeeded", "cancelled"})
DEAD_LETTER_WORK_ITEM_STATE: Final = "dead_letter"

NO_DETAIL: Final[Mapping[str, object]] = MappingProxyType({})

StreamKind = Literal["time_series", "snapshot", "reference"]
# `historical_table` was a third member until 2026-09-04. It named a store no `StreamDefinition` had
# declared since 2026-08-15 and that no statement reads any more; keeping it would leave the type
# advertising a store the report cannot produce. See models.py above `DEFAULT_STREAM_DEFINITIONS`.
StreamStore = Literal["features", "drought_areas"]
StreamVerdict = Literal["complete", "incomplete", "invalid"]
ExpectedFirstDaySource = Literal["declared", "lane_floor", "first_observed", "none"]

# environmental-read-model.ts:2005-2013 `EarliestObservedDateRule`, member for member.
EarliestObservedDayRule = Literal["gap_clustered", "density_floored", "full_history", "no_observations"]


# ---------------------------------------------------------------------------------------------------------------
# What each validity finding breaks downstream. A count with no consequence beside it is a number nobody acts on.
# ---------------------------------------------------------------------------------------------------------------

NULL_GEOMETRY_CHECK: Final = "null_geom"
UNLINKED_GEOMETRY_CHECK: Final = "unlinked_geometry"
MISSING_EXTERNAL_ID_CHECK: Final = "missing_external_id"
MALFORMED_IDENTITY_CHECK: Final = "malformed_identity"
DUPLICATE_IDENTITY_CHECK: Final = "duplicate_identity"
UNDATED_DAY_CHECK: Final = "undated_day"
FUTURE_DAY_CHECK: Final = "future_day"
OUTSIDE_BBOX_CHECK: Final = "outside_bbox"
MISSING_VALUE_SENTINEL_CHECK: Final = "missing_value_sentinel"

VALIDITY_CHECK_ORDER: Final[tuple[str, ...]] = (
    NULL_GEOMETRY_CHECK,
    UNLINKED_GEOMETRY_CHECK,
    MISSING_EXTERNAL_ID_CHECK,
    MALFORMED_IDENTITY_CHECK,
    DUPLICATE_IDENTITY_CHECK,
    UNDATED_DAY_CHECK,
    FUTURE_DAY_CHECK,
    OUTSIDE_BBOX_CHECK,
    MISSING_VALUE_SENTINEL_CHECK,
)

VALIDITY_CHECK_CONSEQUENCES: Final[Mapping[str, str]] = MappingProxyType(
    {
        NULL_GEOMETRY_CHECK: (
            "the row is stored with no shape, so no viewport query can ever return it and no tile can draw it"
        ),
        UNLINKED_GEOMETRY_CHECK: (
            "readObservationWindows requires geometry_id IS NOT NULL, so the row is invisible to the time axis "
            "while the map still draws it -- the slider loses the day, not the pixel"
        ),
        MISSING_EXTERNAL_ID_CHECK: (
            "there is no producer-local key to refresh against, so the next run inserts a second row instead of "
            "updating this one and the duplicate count grows every tick"
        ),
        MALFORMED_IDENTITY_CHECK: (
            "the stored producer-local id is too long for identity.py to namespace, so FeatureIdentity refuses "
            "to rebuild it and the next refresh cannot match the row it is meant to update"
        ),
        DUPLICATE_IDENTITY_CHECK: (
            "two published rows claim one producer-local key, so a viewport draws the feature twice and every "
            "per-feature aggregate double-counts it"
        ),
        UNDATED_DAY_CHECK: (
            "geo.feature_observation_day returns NULL, so the client filter treats the row as undated and shows "
            "it at EVERY date on the slider instead of on its own day"
        ),
        FUTURE_DAY_CHECK: (
            "the observed day is past the server's own day, so the row sits beyond the right edge of the axis "
            "and no date the user can select will reach it"
        ),
        OUTSIDE_BBOX_CHECK: (
            "the row falls outside INGEST_BBOX, so it was written past the bounded-ingestion contract and no "
            "cron tick will ever refresh or retire it"
        ),
        MISSING_VALUE_SENTINEL_CHECK: (
            "-999999 is USGS's 'no reading' marker stored as a JSON number, so it is served as a real "
            "measurement and flattens every colour scale it lands in"
        ),
    }
)

# `geo.drought_areas` rows have no status, no properties, no external id and no geometry link, so these
# checks cannot be answered there and are reported as unevaluated rather than as a reassuring zero. (The
# three `geo.historical_*` tables were the other store this covered, until their statements were deleted
# on 2026-09-04 -- see models.py above `DEFAULT_STREAM_DEFINITIONS`. `drought_areas` is the sole
# remaining caller, in `_read_observations`.)
_FEATURE_ONLY_CHECKS: Final[frozenset[str]] = frozenset(
    {UNLINKED_GEOMETRY_CHECK, MISSING_EXTERNAL_ID_CHECK, MALFORMED_IDENTITY_CHECK, DUPLICATE_IDENTITY_CHECK}
)

# Every `agri.job_definition.name` the durable runtime can actually mint, DERIVED and never spelled here.
# `archive_walk.archive_lane_definition_name` is the only producer of a definition name and
# `lanes.BACKFILL_LANES` is the only registry of lanes it can be handed, so this set is the entire namespace a
# `LaneState.lane` can ever carry.
#
# This is a regression guard, not decoration. This report first shipped naming its lanes `agri.backfill.firms`
# and `agri.backfill.streamflow` -- a parallel namespace matching no row in the ledger. Every stream's lane list
# was therefore EMPTY against production and two guarantees died silently together: `decide_verdict` saw no
# dead-lettered lane, so the 169 dead-lettered FIRMS windows measured on 2026-08-07 were no evidence at all and
# a clean validity sweep reported the stream COMPLETE; and `_resolve_expected_first_day` found no lane floor,
# fell through to `first_observed`, and the 2000->2022 hole the full-archive walk exists to fill measured as
# zero missing days, because a head gap requires `expected_first_day < first_observed`.
ARCHIVE_LANE_DEFINITION_NAMES: Final[frozenset[str]] = frozenset(
    archive_lane_definition_name(lane) for lane in BACKFILL_LANES.values()
)
