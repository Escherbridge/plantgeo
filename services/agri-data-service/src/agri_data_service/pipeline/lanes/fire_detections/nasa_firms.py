"""`fire-detections-direct-forward`'s config-lane strategy: one unit per settled day, products from FIRMS availability.

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
(`foundation/lane_config/AGENTS.md` "Path templates"; `ingest/AGENTS.md` "Path-segment keys"). Every
`area-csv-dated` send carries exactly the OTHER four placeholders that template names -- `source`,
`area`, `day_range`, `start_date` -- and the one `data-availability` send per turn carries no
parameter at all (its template names only `{api_key}`); `split_parameters` routes them all into the
path, so no send has a query string, byte-for-byte the legacy producer's own URL shapes.

**Availability-aware coverage: one unit per settled day, its products read from FIRMS itself.**
This closes the scope limit earlier revisions documented here (a pinned three-product
`expected_units`), for the forward AND the gap-fill path alike. Each day is ONE `SourceRequest`
(the retry grain), and its `fetch`:

1. reads FIRMS' live `data_availability` table through the declared `data-availability` endpoint --
   ONCE per turn, shared by every day unit of that turn (`_TurnAvailability`, keyed on the turn's own
   budgeted client); a failed read is never cached, so the runner's per-unit retry ladder re-asks it;
2. asks `ingest/firms.py::products_covering` which of `FIRMS_HISTORY_SOURCES` (the three live VIIRS
   NRT products, the three VIIRS SP products, MODIS_SP) cover that exact day -- the same port of the
   legacy `pipeline/direct/fire_detections/source.py::fetch_fire_day` read
   (`fetch_product_availability` + `products_covering_span(availability, day, 1)`), whose own
   docstring is "every applicable FIRMS product for one exact settled day as one retry unit";
3. fetches exactly those products, sequentially (so the TOML's `max_concurrency = 3` stays the
   turn's concurrent-send ceiling), and any one of them failing fails the whole unit.

`settle()` then counts `expected_units = len(covering products)` and `present_units = products that
answered`. Three failure modes follow from that, each pinned by a flow test in
`tests/lanes/fire_detections/test_nasa_firms.py`:

* **No forward wedge.** A product NASA retires or renames drops out of the availability table and
  is simply never asked; the day settles from the products that DO cover it (expected = present =
  2, say), instead of failing that product every turn and holding every day `upstream_unavailable`
  forever.
* **No gap-fill under-count.** An old day owes exactly the products that covered it then -- MODIS_SP
  and the SP series for 2012-era days, never "whichever NRT products happen to exist today" -- so a
  day settled short of a covering product can never be written as complete. NRT/SP overlap on a
  reprocessing boundary is collapsed by `ingest/firms.py::collapse_history_records` (standard
  processing supersedes near-real-time), the legacy writer's own precedence rule.
* **Coverage is proven or the day is held -- never guessed.** A covering product failing its fetch
  fails the unit, so on this `partial_day = "refuse"` lane `turn.py::_settle_answered` reports the day
  unwritten under that failure's own reason and nothing is written. An availability read that fails
  fails every unit that needed it the same way: every owed day held, nothing written, and the
  runner's own exit (75 when every sent unit ended `upstream_unavailable`; a parse fault is a per-day
  `strategy_error`). There is no fallback to a pinned constellation. A day the table names NO
  covering product for is a loud per-day `Unsettled` naming the candidates, never a quiet skip and
  never a governed absence.

**Why per-day units, not per-(day, product), and why not `SourceCoverageStrategy`.** The contract's
`SourceCoverageStrategy.source_unit_ids(lane, region)` (`pipeline/runner/contract.py`) is ONE stable
support set for every day, and `turn.py::_settle` rejects any `Written.expected_unit_ids` that differs
from it -- the wrong shape for coverage that varies by era, so this strategy does not implement it
and sets no unit identities (counts only, as before). `plan_requests` is synchronous and runs before
any send, so it cannot know which products cover a day; planning one unit per CANDIDATE product would
reserve seven units of the turn's request cap per day for products never sent. One unit per day
reserves one (`budget.py::select_affordable_days`), while `BudgetedClient` still charges every real
send -- the availability read once per turn (one `request_key`, however often retried), each covering
product once -- and refuses a send past the cap before it leaves (`deferred_budget`, never a partial
day). A day unit's `source` path value is the literal `DAY_UNIT_SOURCE` marker: the unit's key for
budget reservation, never a URL anything sends. Day units are never checkpoint-eligible
(`complete_parameters` is empty): a replay client answers only the one request it holds
(`checkpoints.py::_ReplayClient`) and could never re-read availability, so a retained answer would be
a write that can never restore. The remaining G6 preconditions (gap-fill budget sizing against the
one-reservation-per-day estimate; a product new to FIRMS but absent from `FIRMS_HISTORY_SOURCES` is
never asked) are listed in `pipeline/lanes/fire_detections/AGENTS.md`.

**The forward window re-asks absences, not already-written days.** `lanes/fire-detections-direct-
forward.toml`'s `absence_recheck_days = 5` is the "refetch window" split back from the edge
(`pipeline/runner/AGENTS.md` "Windows": `forward_window = [edge - absence_recheck_days + 1,
edge]`). Inside that window, `turn.py::_series_forward` owes only `_owed_days()` (missing or
incomplete) and re-asks `census.absent_days()` (governed absences, to retract one once FIRMS
starts reporting fires there) -- a day already written with every covering product present is neither,
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

**The 10,000-record cap, surfaced as an explicit UNWRITTEN day, never a silent partial.**
`docs/lanes/fire-detections.md` section 5.1 ("THE HEADLINE TRAP") documents `INGEST_MAX_SOURCE_
RECORDS` defaulting to 10,000 and the legacy ROLLING forward job silently keeping only the newest
10,000 of a merged multi-day, multi-product tick -- a measured, real loss
(memory note `agri-firms-record-cap-drops-silently`; `docs/lanes/fire-detections.md:234-248`). This
strategy's request shape is narrower (one exact day, one send per covering product) than the job that number
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
`settle()` below does not need `DayContext.later_published_day`: a day where every product the availability
table names as covering it answered (no missing member, no cap hit) with zero total rows is an immediate
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

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC
from decimal import ROUND_FLOOR, Decimal
from typing import TYPE_CHECKING, Final

import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.ingest.firms import (
    FIRMS_HISTORY_SOURCES,
    build_fire_detection_write,
    collapse_history_records,
    parse_firms_csv,
    parse_product_availability,
    products_covering,
)
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
    from agri_data_service.ingest.firms import ProductAvailability
    from agri_data_service.ingest.records import FeatureWrite
    from agri_data_service.pipeline.runner.contract import DayContext, ProviderClient, Settlement

#: Matches `lanes/_providers/nasa-firms.toml`'s `[endpoints.area-csv-dated]` declaration.
FORWARD_ENDPOINT: Final = "area-csv-dated"
#: Matches `lanes/_providers/nasa-firms.toml`'s `[endpoints.data-availability]` declaration.
AVAILABILITY_ENDPOINT: Final = "data-availability"
#: Every product a day may owe; which of them it DOES owe is read from FIRMS per turn (module docstring).
CANDIDATE_PRODUCTS: Final = FIRMS_HISTORY_SOURCES
#: A day unit's `source` path value: its budget/report key, never sent (module docstring, "Why per-day units").
DAY_UNIT_SOURCE: Final = "AVAILABILITY_COVERED"
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


@dataclass(frozen=True, slots=True)
class ProductAnswer:
    """One covering product's parsed CSV for the day."""

    product: str
    features: list[dict[str, object]]


@dataclass(frozen=True, slots=True)
class FirmsDay:
    """A day unit's payload: the products availability names as covering the day, and each one's answer."""

    covering: tuple[str, ...]
    answers: tuple[ProductAnswer, ...]


@dataclass(frozen=True, slots=True)
class _Availability:
    """One parsed `data_availability` read and when it was retrieved."""

    table: Mapping[str, ProductAvailability]
    retrieved_at: datetime


@dataclass(slots=True)
class _TurnAvailability:
    """Memoization: one `data_availability` read per turn, shared by every day unit the turn's client fetches.

    Keyed on the client's identity (the runner builds one `BudgetedClient` per turn). A failed read is
    dropped, never cached, so the runner's per-unit retry ladder re-asks it; concurrent units await one
    shielded in-flight read, so a sibling's per-unit timeout never cancels it for the others.
    """

    client: object = None
    task: asyncio.Future[_Availability] | None = None

    async def read(self, client: ProviderClient) -> _Availability:
        """The turn's availability: the in-flight or finished read for this client, else a new one."""
        task = self.task
        if task is None or self.client is not client:
            self.client = client
            task = self.task = asyncio.ensure_future(_read_availability(client))
        try:
            return await asyncio.shield(task)
        except Exception:
            if self.task is task:
                self.task = None
            raise


async def _read_availability(client: ProviderClient) -> _Availability:
    """The live per-product coverage windows (`ingest/firms.py::parse_product_availability`)."""
    response = await client.get(AVAILABILITY_ENDPOINT, {})
    return _Availability(
        table=parse_product_availability(response.body.decode("utf-8")), retrieved_at=response.retrieved_at
    )


def _firms_day(responses: Sequence[SourceResponse]) -> FirmsDay:
    """The one day unit's payload (`plan_requests` plans exactly one unit per day)."""
    days = [response.payload for response in responses if isinstance(response.payload, FirmsDay)]
    if len(days) != 1:
        raise ValueError(f"expected exactly one FIRMS day answer, got {len(days)}")
    return days[0]


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
    """One unit per settled day, owing exactly the products FIRMS availability names; never a partial day."""

    _availability: _TurnAvailability = field(default_factory=_TurnAvailability, compare=False, repr=False)

    def plan_requests(self, days: Sequence[date], lane: LaneConfig, region: Region) -> list[SourceRequest]:  # noqa: ARG002 - the Protocol's lane and region; bbox comes from INGEST_BBOX (matches watersheds.nhdplus)
        """One request per day; gracefully plans nothing when `INGEST_BBOX` is unset."""
        bbox = resolve_bounded_bbox(None)
        if bbox is None:
            # No raise: `plan_requests` raising aborts the WHOLE turn (exit 70, a code-fault
            # severity), the wrong weight for an operator forgetting an env var. Mirrors
            # watersheds.nhdplus's identical graceful-empty-plan choice for the same gap.
            return []
        return [
            SourceRequest(
                unit=day.isoformat(),
                endpoint=FORWARD_ENDPOINT,
                days=(day,),
                parameters=(
                    ("source", DAY_UNIT_SOURCE),
                    ("area", bbox),
                    ("day_range", _DAY_RANGE),
                    ("start_date", day.isoformat()),
                ),
                requested_parameters=_REQUESTED_PARAMETERS,
            )
            for day in days
        ]

    async def fetch(self, request: SourceRequest, client: ProviderClient) -> SourceResponse:
        """Read the turn's availability, then fetch every product covering the day; any failure fails the unit."""
        day = request.days[0]
        availability = await self._availability.read(client)
        covering = products_covering(availability.table, day, CANDIDATE_PRODUCTS)
        answers: list[ProductAnswer] = []
        bodies: list[bytes] = []
        retrieved_at = availability.retrieved_at
        # Sequential: the TOML's max_concurrency stays the turn's concurrent-send ceiling.
        for product in covering:
            response = await client.get(request.endpoint, {**request.query(), "source": product})
            features = parse_firms_csv(response.body.decode("utf-8"), product)
            answers.append(ProductAnswer(product=product, features=features))
            bodies.append(product.encode("utf-8") + b"\n" + response.body)
            retrieved_at = max(retrieved_at, response.retrieved_at)
        return SourceResponse(
            request=request,
            body=b"\n".join(bodies),
            retrieved_at=retrieved_at,
            # Never checkpoint-eligible: a replay could not re-read availability (module docstring).
            complete_parameters=frozenset(),
            payload=FirmsDay(covering=covering, answers=tuple(answers)),
        )

    def settle(self, day: date, responses: Sequence[SourceResponse], context: DayContext) -> Settlement:  # noqa: ARG002 - absence here is unconditional (module docstring); no mirrored-past proof needed
        """Expected = the day's covering products; none covering is a loud refusal, a capped one never complete."""
        if not responses:
            return Unsettled("upstream_unavailable", f"no FIRMS answer for {day.isoformat()}")
        firms_day = _firms_day(responses)
        if not firms_day.covering:
            return Unsettled(
                "unsettled",
                f"FIRMS data_availability names no product covering {day.isoformat()} (candidates: "
                f"{', '.join(CANDIDATE_PRODUCTS)}); refusing rather than certifying a fire-free day",
            )
        capped = sorted(
            answer.product for answer in firms_day.answers if len(answer.features) >= FIRMS_RESPONSE_RECORD_CAP
        )
        if capped:
            return Unsettled(
                "unsettled",
                f"{', '.join(capped)} returned >= {FIRMS_RESPONSE_RECORD_CAP} record(s) for {day.isoformat()}; a "
                "response at FIRMS' own silent per-request truncation point can never be proven complete -- see "
                'this module\'s docstring ("The 10,000-record cap") and docs/lanes/fire-detections.md section 5.1',
            )
        expected_units = len(firms_day.covering)
        present_units = len({answer.product for answer in firms_day.answers} & set(firms_day.covering))
        if present_units == expected_units and not any(answer.features for answer in firms_day.answers):
            return Absent(
                reason="every FIRMS product covering the day answered with zero detections",
                proof=f"{', '.join(firms_day.covering)} each answered {day.isoformat()} with zero rows",
            )
        # A short answer is a partial `Written`, which the runner refuses on this `refuse` lane.
        return Written(expected_units=expected_units, present_units=present_units)

    def rows(self, day: date, responses: Sequence[SourceResponse]) -> pa.Table:
        """One row per valued 0.005-degree cell; any identity or geometry defect refuses the whole day."""
        retrieved_at = max(response.retrieved_at for response in responses)
        features = [feature for answer in _firms_day(responses).answers for feature in answer.features]
        writes: dict[str, FeatureWrite] = {}
        rejected = 0
        # SP supersedes NRT on a reprocessing overlap, deterministically (ingest/firms.py precedence rule).
        for feature in collapse_history_records(features):
            write = build_fire_detection_write(feature, FIRE_DETECTIONS_STREAM, None, retrieved_at)
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
    "AVAILABILITY_ENDPOINT",
    "CANDIDATE_PRODUCTS",
    "DAY_UNIT_SOURCE",
    "FIRMS_RESPONSE_RECORD_CAP",
    "FORWARD_ENDPOINT",
    "STRATEGY",
    "FirmsDay",
    "NasaFirmsForwardStrategy",
    "ProductAnswer",
]
