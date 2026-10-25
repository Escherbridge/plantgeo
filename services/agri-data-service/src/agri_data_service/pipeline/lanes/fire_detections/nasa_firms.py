"""`fire-detections-direct-forward`'s config-lane strategy: one request per (settled day, VIIRS NRT product).

Strategy key `fire_detections.nasa_firms` (`pipeline/lanes/AGENTS.md` "Config-lane strategies").
Ships DARK: `lanes/fire-detections-direct-forward.toml` declares `executor = "legacy"`, so
`pipeline.direct.fire_detections` keeps writing unchanged; this module exists so the TOML and
strategy pass their own review and are ready for a future G6-equivalent cut-over. A NEW package --
`fire_detections` was never part of the `p4-extract` move (`pipeline/lanes/AGENTS.md`, "Extracted
source leaves are NOT strategies" lists the packages that WERE moved; this is not one of them), so
everything here is authored fresh beside `pipeline/direct/fire_detections/**`, which this module
never imports (`pipeline/lanes/AGENTS.md`: "never `pipeline/direct/**`").

**Behaviour derived from, never imported from, the legacy direct writer.** `pipeline/direct/
fire_detections/{source,rows,adapter}.py` is READ-ONLY reference: this module reimplements the
same cell-day floor aggregation and the same "any defect refuses the whole day" policy
(`forward.py::WRITER_CONTRACT`: `identity_defect=REFUSE_WHOLE_RELEASE`,
`geometry_defect=REFUSE_WHOLE_RELEASE`) in the runner's own vocabulary, so a future G6 cut-over
would publish the same shape of day the legacy writer already does. `ingest/firms.py` IS imported
(the namespace rule the batch-1 `wfigs` lane also leans on: a strategy may import `ingest/**`),
for CSV parsing, identity, and the live VIIRS NRT product list, so neither this module nor the
legacy writer drifts from the one producer contract both ultimately read.

**Always the DATED endpoint, never the bare one.** `lanes/_providers/nasa-firms.toml` declares two
forward-shaped endpoints, `area-csv` (today's rolling window) and `area-csv-dated` (one exact past
day). Every request this strategy plans names an exact already-settled UTC day
(`start_date=day`), matching `ingest/firms.py::fetch_active_fires`'s own dated branch and the
legacy direct writer's identical choice (`pipeline/direct/fire_detections/source.py`) -- never
FIRMS' bare "now" window, which this lane's forward cadence has no use for.

**`{api_key}` is never touched here.** `nasa-firms.toml` declares `api_key_transport = "path"`: the
reserved `{api_key}` path segment is filled by the provider client alone, from `api_key_env`
(`NASA_FIRMS_KEY`, named, never a value) under the endpoint's `customer_host` opt-in
(`foundation/lane_config/AGENTS.md` "Path templates"; `ingest/AGENTS.md` "Path-segment keys"). This
strategy's `SourceRequest.parameters` carries exactly the OTHER four placeholders the
`area-csv-dated` template names -- `source`, `area`, `day_range`, `start_date` -- and nothing else;
`split_parameters` routes all four into the path, so the send URL has no bare `?` and no query
string, byte-for-byte the legacy producer's own URL shape.

**Coverage unit = one VIIRS NRT product, not one HTTP page.** `Written.expected_units`/
`present_units` count how many of the three live constellation members
(`ingest/firms.py::FIRMS_VIIRS_SOURCES`: `VIIRS_SNPP_NRT`, `VIIRS_NOAA20_NRT`, `VIIRS_NOAA21_NRT`)
answered for a day, mirroring the legacy source's own fan-out
(`pipeline/direct/fire_detections/source.py::fetch_fire_day`'s `asyncio.gather` over exactly those
three). This restates, in the runner's vocabulary, the legacy adapter's hand-written "a missing
constellation member refuses that day; it must not look like a quiet fire day"
(`pipeline/direct/fire_detections/AGENTS.md`).

**Two different mechanisms answer to that one sentence, and only one of them fires in practice
under this TOML's `partial_day = "refuse"`.** When a unit's FETCH itself fails (exhausts the
runner's own retry ladder: `SourceUnavailableError`/`SourceThrottledError`/a transport fault),
`turn.py::_settle_answered` never calls this strategy's `settle()` for that day at all on a
`refuse` lane -- it reports the day unwritten directly, under the failed unit's OWN reason (e.g.
`upstream_unavailable`), and the other two products' successful answers are discarded with it. The
`present_units < expected_units` branch inside `settle()` below is therefore DEFENSIVE, not the
path a genuinely down satellite takes today: it exists for correctness (so this strategy never
mislabels a short response as a governed absence) and for a possible future where
`partial_day` is `"write_and_recheck"`, under which the runner DOES hand `settle()` whatever subset
of units answered. `tests/lanes/fire_detections/test_nasa_firms.py::
test_a_missing_constellation_member_refuses_the_whole_day_never_a_quiet_fire_day` pins the actual
(`upstream_unavailable`, direct-report) path.

**The forward window re-asks absences, not already-written days.** `lanes/fire-detections-direct-
forward.toml`'s `absence_recheck_days = 5` is the "refetch window" split back from the edge
(`pipeline/runner/AGENTS.md` "Windows": `forward_window = [edge - absence_recheck_days + 1,
edge]`). Inside that window, `turn.py::_series_forward` owes only `_owed_days()` (missing or
incomplete) and re-asks `census.absent_days()` (governed absences, to retract one once FIRMS
starts reporting fires there) -- a day already written with all three products present is neither,
so it is NOT re-fetched again by a later turn. This is a DELIBERATE, narrower behaviour than the
legacy hourly job, which blindly re-walks every day in its window including already-complete ones
(`docs/lanes/fire-detections.md` section 2: "Each turn refreshes the bounded settled window...
including already-complete direct-owned days"). The `refuse` nature's narrower recheck loses
nothing that mattered: the legacy citation's own worked example is a day that STARTS empty and
LATER acquires detections -- exactly the absence-retraction path `census.absent_days()` re-asks
every turn for the full 5-day window -- not a day that was already complete gaining yet more
detections after the fact (NRT's constellation members do not retroactively revise an
already-reported hotspot; MODIS/VIIRS SP reprocessing, which genuinely does revise a day, is
archive-only and out of this forward lane's realistic window). `tests/lanes/fire_detections/
test_nasa_firms.py::test_an_initially_absent_day_is_re_asked_and_its_absence_retracted` pins the
retraction path this mechanism actually exercises.

**A SCOPE LIMIT, stated rather than hidden: `expected_units` is pinned to the three LIVE NRT
products and this strategy never consults FIRMS' `data_availability` table.** The legacy source
module always reads that table first and filters to the products that actually cover a day
(`fetch_product_availability` / `products_covering_span`) because its archive walker also reaches
into `VIIRS_*_SP`/`MODIS_SP` territory, where product coverage genuinely varies. This lane's
realistic forward window (`absence_recheck_days = 5`, back from `today - publication_lag_days`)
only ever lands inside each live VIIRS NRT product's already-active coverage, so skipping the
availability read costs nothing today and saves one extra request per turn. It is NOT safe to
enable `gap_fill_enabled` against this module as written, for a quieter reason than a hard refusal:
`ingest/firms.py`'s own documented behaviour is that a product asked for a day it does not cover
answers with FEWER rows, never an error ("over-asking is safe and cheap"), so a pre-2018 day would
likely still settle and write -- using only whichever of the three NRT products happened to exist
yet, silently omitting every `VIIRS_*_SP`/`MODIS_SP` detection the legacy archive walker's
availability-aware fan-out would have included. That is UNDER-counted history served as if it were
complete, not a loud refusal -- worse, not better, and exactly the failure mode
`SourceCoverageStrategy` exists to prevent. Porting `fetch_product_availability`/
`products_covering_span` into this strategy (or an equivalent `SourceCoverageStrategy`) is the
prerequisite for ever flipping `gap_fill_enabled` here; tracked, not fixed, in this slice.

The pinned constellation has a FORWARD-path cost too (ring review, 2026-10-25): if NASA retires or
renames one of the three NRT tokens, that product fails every fetch, and on a `refuse` lane a day
with any failed unit never reaches `settle()` -- every day is held `upstream_unavailable` forever,
indistinguishable from a transient 503. The same availability-aware fan-out (or an alert on a
persistent single-product failure streak) is therefore a G6 prerequisite for the FORWARD path as
well, not only for gap-fill.

**The 10,000-record cap, surfaced as an explicit UNWRITTEN day, never a silent partial.**
`docs/lanes/fire-detections.md` section 5.1 ("THE HEADLINE TRAP") documents `INGEST_MAX_SOURCE_
RECORDS` defaulting to 10,000 and the legacy ROLLING forward job silently keeping only the newest
10,000 of a merged multi-day, multi-product tick -- a measured, real loss
(memory note `agri-firms-record-cap-drops-silently`; `docs/lanes/fire-detections.md:234-248`). This
strategy's request shape is narrower (one exact day, one product per unit) than the job that number
was measured against, but the number itself is the one piece of upstream behaviour this repo has
ever measured against FIRMS' per-request CSV answer, so `settle()` below treats ANY single
product-day response landing at or above that same threshold as a tripwire: such a response can
never be proven complete (a response sitting exactly at a known truncation point is indistinguishable
from one that was cut there), so the day is reported `Unsettled` -- visible in the S5 report's
`unwritten` list every turn -- rather than either (a) silently writing a truncated sample as if it
were the whole day, or (b) silently dropping the overflow the way the legacy rolling job does. The
next hourly turn re-asks it inside the 5-day `absence_recheck_days` window; a day that keeps
tripping the cap stays visibly `unsettled` rather than quietly wrong.

**Governed absence is unconditional here, unlike `drought.usdm_releases`' mirrored-past pattern.**
`settle()` below does not need `DayContext.later_published_day`: a day where every one of the three
live products answered (no missing member, no cap hit) with zero total rows is an immediate
`Absent` -- directly mirroring the legacy adapter's own unconditional governed absence
(`pipeline/direct/fire_detections/adapter.py`: "all {products} requests succeeded... and yielded
zero accepted records"). USDM's 404 needed a mirrored-past proof because a 404 is ambiguous
("not yet" vs "never"); a FIRMS product answering its CSV endpoint with a header and zero rows is
not ambiguous in the same way -- it is a successful, already-settled empty answer.

**`Written.source_digest` is left `None` on purpose.** Unlike `drought.usdm_releases`'
`ingested_at` or `fire_perimeters.wfigs`'s `updated_at`, this stream's rows carry no capture-time
column at all (`warehouse/schemas/fire_detections.py`'s `FIRE_DETECTIONS_SCHEMA`; the TOML's
`[[streams]]` comment states this explicitly, so no `volatile_columns` entry is needed either). The
built row content is therefore deterministic given the same detections, so the runner's own
rows-table digest (`Written.source_digest`'s docstring: "`None` lets the runner digest the day's
rows instead") already does the right thing wherever this strategy IS re-asked for a day it has
an opinion on: an unchanged governed absence re-confirms with no spurious write, and (per "The
forward window re-asks absences" above) a day whose absence retracts writes exactly once, with
zero extra digest-exclusion code here.

**Identity and geometry defects refuse the whole day, from `rows()`, never a silent drop.** Any
unkeyable/invalid detection, any detection landing outside the requested exact day, or any
non-finite coordinate/FRP value raises `ValueError` from `rows()` -- the runner's own vocabulary
turns a `rows()` fault into that day's `strategy_error`, never the turn's
(`pipeline/runner/AGENTS.md`: "A strategy fault in settle/rows/derive is that day's strategy_error,
never the turn's"). This restates, rather than relaxes, the legacy writer's
`identity_defect=REFUSE_WHOLE_RELEASE` / `geometry_defect=REFUSE_WHOLE_RELEASE` policy.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC
from decimal import ROUND_FLOOR, Decimal
from typing import TYPE_CHECKING, Final

import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.ingest.firms import FIRMS_VIIRS_SOURCES, build_fire_detection_write, parse_firms_csv
from agri_data_service.ingest.policy import resolve_bounded_bbox
from agri_data_service.pipeline.runner.contract import Absent, SourceRequest, SourceResponse, Unsettled, Written
from agri_data_service.warehouse.schemas.fire_detections import (
    FIRE_DETECTIONS_CELL_SIZE_DEGREES,
    FIRE_DETECTIONS_SCHEMA,
    FIRE_DETECTIONS_STREAM,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date, datetime

    from agri_data_service.foundation.lane_config.models import LaneConfig
    from agri_data_service.foundation.region.manifest import Region
    from agri_data_service.ingest.records import FeatureWrite
    from agri_data_service.pipeline.runner.contract import DayContext, ProviderClient, Settlement

#: Matches `lanes/_providers/nasa-firms.toml`'s `[endpoints.area-csv-dated]` declaration.
FORWARD_ENDPOINT: Final = "area-csv-dated"
#: Every placeholder the dated template names besides the reserved `{api_key}`.
_REQUESTED_PARAMETERS: Final = frozenset({"source", "area", "day_range", "start_date"})
#: One exact settled UTC day per request; never a rolling span (see module docstring).
_DAY_RANGE: Final = "1"
#: The real, measured upstream truncation point this repo has evidence for (module docstring,
#: "The 10,000-record cap"). Hit per single product-day response, never the legacy rolling job's
#: multi-day merged total.
FIRMS_RESPONSE_RECORD_CAP: Final = 10_000
#: A GeoJSON Point's `[longitude, latitude]`.
_POINT_COORDINATE_COUNT: Final = 2
#: `Decimal(str(...))`, never `Decimal(float)`: avoids binary-float imprecision in the floor-snap.
_CELL_SIZE: Final = Decimal(str(FIRE_DETECTIONS_CELL_SIZE_DEGREES))


def _product_of(response: SourceResponse) -> str:
    """The VIIRS NRT product token this response answered for, read back off its own request."""
    return dict(response.request.parameters).get("source", "?")


def _record_count(response: SourceResponse) -> int:
    """How many raw CSV rows this response parsed to, before identity/geometry validation."""
    return len(response.payload) if isinstance(response.payload, list) else 0


def _floor_cell(value: Decimal) -> Decimal:
    """Floor-snap one ordinate to the registered 0.005-degree grid (`FIRE_DETECTIONS_CELL_SIZE_DEGREES`)."""
    return (value / _CELL_SIZE).to_integral_value(rounding=ROUND_FLOOR) * _CELL_SIZE


def _finite_decimal(value: object, *, field: str, external_id: str) -> Decimal:
    """Parse one numeric field as a finite Decimal, or refuse the whole day -- never a silently dropped ordinate."""
    try:
        parsed = Decimal(str(value))
    except Exception as error:
        raise ValueError(f"FIRMS detection {external_id!r} has an invalid {field}: {value!r}") from error
    if not parsed.is_finite():
        raise ValueError(f"FIRMS detection {external_id!r} has a non-finite {field}: {value!r}")
    return parsed


@dataclass(slots=True)
class _CellTotals:
    """Typed additive values for one 0.005-degree fire cell, mirroring the legacy direct writer's grain."""

    newest_observed_at: datetime
    detection_count: int = 0
    frp_sum: Decimal = Decimal("0")
    frp_observation_count: int = 0
    high_confidence_detection_count: int = 0


@dataclass(frozen=True, slots=True)
class NasaFirmsForwardStrategy:
    """One unit per (settled day, live VIIRS NRT product); never a partial day (`partial_day = "refuse"`)."""

    def plan_requests(self, days: Sequence[date], lane: LaneConfig, region: Region) -> list[SourceRequest]:  # noqa: ARG002 - the Protocol's lane and region; bbox comes from INGEST_BBOX (matches watersheds.nhdplus)
        """One request per (day, product); gracefully plans nothing when `INGEST_BBOX` is unset."""
        bbox = resolve_bounded_bbox(None)
        if bbox is None:
            # No raise: `plan_requests` raising aborts the WHOLE turn (exit 70, a code-fault
            # severity), the wrong weight for an operator forgetting an env var. Mirrors
            # watersheds.nhdplus's identical graceful-empty-plan choice for the same gap.
            return []
        return [
            SourceRequest(
                unit=f"{day.isoformat()}:{product}",
                endpoint=FORWARD_ENDPOINT,
                days=(day,),
                parameters=(
                    ("source", product),
                    ("area", bbox),
                    ("day_range", _DAY_RANGE),
                    ("start_date", day.isoformat()),
                ),
                requested_parameters=_REQUESTED_PARAMETERS,
            )
            for day in days
            for product in FIRMS_VIIRS_SOURCES
        ]

    async def fetch(self, request: SourceRequest, client: ProviderClient) -> SourceResponse:
        """Fetch one product-day CSV and parse it; FIRMS answers an uncovered day with an empty body, never a 404."""
        response = await client.get(request.endpoint, request.query())
        product = dict(request.parameters)["source"]
        features = parse_firms_csv(response.body.decode("utf-8"), product)
        return SourceResponse(
            request=request,
            body=response.body,
            retrieved_at=response.retrieved_at,
            complete_parameters=request.requested_parameters,
            payload=features,
        )

    def settle(self, day: date, responses: Sequence[SourceResponse], context: DayContext) -> Settlement:  # noqa: ARG002 - absence here is unconditional (module docstring); no mirrored-past proof needed
        """A missing member or a capped response never settles as complete; zero-of-three is an immediate absence."""
        if not responses:
            return Unsettled("upstream_unavailable", f"no VIIRS NRT product answered for {day.isoformat()}")
        capped = sorted(
            {_product_of(response) for response in responses if _record_count(response) >= FIRMS_RESPONSE_RECORD_CAP}
        )
        if capped:
            return Unsettled(
                "unsettled",
                f"{', '.join(capped)} returned >= {FIRMS_RESPONSE_RECORD_CAP} record(s) for {day.isoformat()}; a "
                "response at FIRMS' own silent per-request truncation point can never be proven complete -- see "
                'this module\'s docstring ("The 10,000-record cap") and docs/lanes/fire-detections.md section 5.1',
            )
        present_units = len(responses)
        expected_units = len(FIRMS_VIIRS_SOURCES)
        if present_units < expected_units:
            # Defensive, not the live path for a fetch failure (module docstring, "Two different
            # mechanisms"): a genuinely down satellite never reaches here on this TOML's
            # `partial_day = "refuse"`. Kept so a response SHORTAGE this strategy itself ever
            # produces (never a fetch failure) is still reported as a partial `Written`, never
            # silently folded into a governed absence.
            return Written(expected_units=expected_units, present_units=present_units)
        if sum(_record_count(response) for response in responses) == 0:
            return Absent(
                reason="every live VIIRS NRT product answered with zero detections",
                proof=f"{', '.join(sorted(FIRMS_VIIRS_SOURCES))} each answered {day.isoformat()} with zero rows",
            )
        return Written(expected_units=expected_units, present_units=present_units)

    def rows(self, day: date, responses: Sequence[SourceResponse]) -> pa.Table:
        """One row per valued 0.005-degree cell; any identity or geometry defect refuses the whole day."""
        writes: dict[str, FeatureWrite] = {}
        rejected = 0
        for response in responses:
            features = response.payload if isinstance(response.payload, list) else []
            for feature in features:
                write = build_fire_detection_write(feature, FIRE_DETECTIONS_STREAM, None, response.retrieved_at)
                if write is None:
                    rejected += 1
                    continue
                observed_at = write.identity.observed_at
                if observed_at is None or observed_at.astimezone(UTC).date() != day:
                    raise ValueError(
                        f"FIRMS answered the exact-day request for {day.isoformat()} with a detection observed at "
                        f"{observed_at}; refusing the whole day rather than writing it under the wrong partition"
                    )
                writes[write.external_id] = write
        if rejected:
            raise ValueError(
                f"FIRMS returned {rejected} unkeyable or invalid detection(s) for {day.isoformat()}; refusing the "
                'whole day rather than writing a partial one (this lane\'s partial_day = "refuse" policy)'
            )
        cells: dict[tuple[Decimal, Decimal], _CellTotals] = {}
        for write in writes.values():
            geometry = write.properties.get("geometry")
            coordinates = geometry.get("coordinates") if isinstance(geometry, Mapping) else None
            if not isinstance(coordinates, list) or len(coordinates) < _POINT_COORDINATE_COUNT:
                msg = f"FIRMS detection {write.external_id!r} has no point coordinates for {day.isoformat()}"
                raise ValueError(msg)
            longitude = _finite_decimal(coordinates[0], field="longitude", external_id=write.external_id)
            latitude = _finite_decimal(coordinates[1], field="latitude", external_id=write.external_id)
            cell = (_floor_cell(longitude), _floor_cell(latitude))
            observed_at = write.identity.observed_at
            if observed_at is None:  # pragma: no cover - already refused above when None
                raise ValueError(f"FIRMS detection {write.external_id!r} lost its observation timestamp")
            held = cells.setdefault(cell, _CellTotals(newest_observed_at=observed_at))
            held.detection_count += 1
            frp = write.properties.get("frp")
            if isinstance(frp, int | float) and not isinstance(frp, bool):
                held.frp_sum += _finite_decimal(frp, field="frp", external_id=write.external_id)
                held.frp_observation_count += 1
            if write.properties.get("confidenceNormalized") == "high":
                held.high_confidence_detection_count += 1
            held.newest_observed_at = max(held.newest_observed_at, observed_at)
        table_rows = [
            {
                "cell_longitude": float(longitude),
                "cell_latitude": float(latitude),
                "observed_day": day,
                "detection_count": totals.detection_count,
                "frp_sum": float(totals.frp_sum) if totals.frp_observation_count else None,
                "frp_observation_count": totals.frp_observation_count,
                "high_confidence_detection_count": totals.high_confidence_detection_count,
                "newest_observed_at": totals.newest_observed_at,
            }
            for (longitude, latitude), totals in sorted(cells.items())
        ]
        return pa.Table.from_pylist(table_rows, schema=FIRE_DETECTIONS_SCHEMA.arrow_schema)


#: The single instance the lane TOML's `strategy = "fire_detections.nasa_firms"` resolves to (S14).
STRATEGY: Final = NasaFirmsForwardStrategy()


__all__ = [
    "FIRMS_RESPONSE_RECORD_CAP",
    "FORWARD_ENDPOINT",
    "STRATEGY",
    "NasaFirmsForwardStrategy",
]
