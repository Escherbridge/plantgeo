# `pipeline/lanes/fire_detections/` — the fire-detections config-lane strategy package

A NEW package (`s-fire-detections`, track `config_driven_ingestion_20260926`, Phase 4): `p4-extract`
moved nothing here, because fire-detections' legacy modules
(`pipeline/direct/fire_detections/**`) were never shared with a non-lane caller, unlike
`evacuation_zones`/`watersheds`/`burn_severity`/`drought`. Everything below is authored fresh beside
`pipeline/direct/fire_detections/**`, which stays the active legacy implementation
(`executor = "legacy"` in the shipped TOML) until a future G6-equivalent cut-over.

| module | what |
|---|---|
| `nasa_firms.py` | the strategy (`STRATEGY`): plans one unit per settled day; its fetch reads FIRMS' `data_availability` once per turn and asks exactly the products covering that day; settles (no-coverage refusal / capped / absent / written), and floor-aggregates to the registered cell-day grain |

Imports: `foundation`, `ingest` (specifically `ingest/firms.py` and `ingest/policy.py`, per the
namespace rule the batch-1 `fire_perimeters.wfigs` lane also leans on), `warehouse`,
`pipeline/runner/contract.py` (never any other runner module, S14) and this package only. Never
another layer's `pipeline/lanes/<layer>/` and never `pipeline/direct/**`
(`tests/test_layer_import_contract.py::test_lanes_do_not_import_each_other`).

## Why this lane needs no `_shared` import

`_shared/geometry.py` holds the three DuckDB spatial-repair chains the vector lanes
(watersheds/drought/burn_severity/evacuation_zones/fire_perimeters) need because their rows carry
polygon geometry. Fire detections are POINTS (`[longitude, latitude]` straight off the parsed CSV),
floor-snapped to a grid cell with plain `Decimal` arithmetic -- there is no WKB conversion and no
DuckDB session anywhere in this lane. `_shared/digest.py` is unused for the same reason
`nasa_firms.py`'s module docstring gives for `Written.source_digest = None`: this stream's rows
carry no capture-time column, so there is nothing to canonical-JSON-digest to keep a refetch from
reading as a revision -- the runner's own rows-table digest already does the right thing.

## Why `expected_units` counts covering FIRMS products, not grid cells

Unlike the `grid_refuse` conformance fixture (`tests/runner/fixtures/grid_refuse.py`), whose
`Written.expected_units` is the GRID's own cell count, this lane's coverage unit is "did every
product FIRMS says covers this day answer" -- there is no fixed cell population to compare against;
a settled day's true detection count is whatever FIRMS published, zero included. The expected set
is read per turn from FIRMS' own `data_availability` table, restricted to
`ingest/firms.py::FIRMS_HISTORY_SOURCES` (`ingest/firms.py::products_covering`), so it is 3 for a
2026 day (the NRT trio), 1 for a 2000 day (MODIS_SP), and whatever the table says after a satellite
is retired. A covering product that fails its fetch fails the whole day unit, so the day is held
one level UP from `settle()`: `turn.py::_settle_answered` reports it under the failure's own reason
on this `partial_day = "refuse"` lane and writes nothing.

## The two "10,000" numbers in this codebase, and why only one of them is this module's concern

`ingest/policy.py::INGEST_MAX_SOURCE_RECORDS` (default 10,000) is the LEGACY rolling hourly
Postgres job's own silent-truncation knob, applied to one tick's MERGED multi-day, multi-product
deduplicated set (`docs/lanes/fire-detections.md` section 5.1). `pipeline/direct/fire_detections/
products.py::FIRE_DIRECT_DEFAULT_MAX_RECORDS_PER_DAY` (50,000) is the legacy DIRECT writer's own
separate fail-closed ceiling, applied per exact settled day, hard-refusing above it (never a silent
drop). This module introduces a THIRD, narrower check, reusing the first number's VALUE (10,000, the
one upstream-truncation figure this repo has ever measured) against a PER-PRODUCT-PER-DAY response
-- the narrowest possible request shape this lane ever sends -- and maps a hit to an explicit
`Unsettled` day rather than either sibling's behaviour. See `nasa_firms.py`'s module docstring,
"The 10,000-record cap", for the full reasoning; `docs/lanes/fire-detections.md` section 5.1 is the
evidence trail for the number itself.

## Availability-aware coverage: the known gap is CLOSED (2026-10-25, owner-approved)

Earlier revisions pinned `expected_units = 3` (the live NRT trio) and skipped FIRMS'
`data_availability` read, which had two failure modes: a FORWARD WEDGE (a retired/renamed NRT token
fails every fetch, so every day is held `upstream_unavailable` forever) and a GAP-FILL UNDER-COUNT
(an old day settled from only the products existing then, silently omitting SP/MODIS detections).
Both are closed by the same port of the legacy `pipeline/direct/fire_detections/source.py::
fetch_fire_day` read (never imported): one unit per day, whose fetch reads the availability table
once per turn through the declared `data-availability` endpoint and asks only the products covering
that day. An availability read that fails holds every day that needed it -- there is no fallback to
a pinned constellation. A day no product covers is a loud per-day `Unsettled`, never an absence.
`nasa_firms.py`'s module docstring ("Availability-aware coverage", "Why per-day units") has the
mechanism and why it is NOT `SourceCoverageStrategy` (that Protocol is one static support set per
lane, and `turn.py::_settle` rejects any per-day difference from it).

**Remaining G6 preconditions, stated honestly:**

* **Gap-fill budget sizing.** `select_affordable_days` reserves ONE request per day unit, but a day
  really costs one send per covering product (plus the turn's single availability read).
  `BudgetedClient` still refuses any send past the cap (the unit is `deferred_budget`, never
  partial), so this is safe, but a 90-call gap-fill turn selects ~90 days and finishes roughly
  90 / (covering products per day) of them, wasting the tail unit's partial sends. Re-size
  `gap_fill_max_weighted_calls` (or the day cap) against measured coverage before flipping
  `gap_fill_enabled`. The forward turn is unaffected: 5 days x 3 + 1 = 16 sends under its cap of 18.
* **A product NEW to FIRMS is not asked.** Candidates are `ingest/firms.py::FIRMS_HISTORY_SOURCES`;
  a product the availability table names that is not in that tuple (e.g. a future
  `VIIRS_NOAA22_NRT`) is ignored, so its detections would be missing without any signal. Adding it
  is a one-line `ingest/firms.py` change; nothing alerts on it yet.
* **No retry-grain finer than the day.** One covering product's 503 re-sends that day's other
  products on each retry (attempts only -- `budget.py::TurnBudget.charge` counts a logical request
  once). This mirrors the legacy writer's own day retry unit.
* **No source checkpoints.** Day units are never checkpoint-eligible (a replay could not re-read
  availability), so a held day is re-fetched in full next turn.
* `gap_fill_enabled` stays `false` until the owner flips it inside gate G6 (S12).

## Rationale relocated from `nasa_firms.py`'s module docstring

The module docstring itself now carries only a one-line summary and a pointer here; the sections
below are its full original text, moved verbatim so the code stays terse per this repo's
directory-doc convention.

### Behaviour derived from, never imported from, the legacy direct writer

`pipeline/direct/fire_detections/{source,rows,adapter}.py` is READ-ONLY reference: this module
reimplements the same cell-day floor aggregation and the same "any defect refuses the whole day"
policy (`forward.py::WRITER_CONTRACT`: `identity_defect=REFUSE_WHOLE_RELEASE`,
`geometry_defect=REFUSE_WHOLE_RELEASE`) in the runner's own vocabulary, so a future G6 cut-over
would publish the same shape of day the legacy writer already does. `ingest/firms.py` IS imported
(the namespace rule the batch-1 `wfigs` lane also leans on: a strategy may import `ingest/**`),
for CSV parsing, identity, and the live VIIRS NRT product list, so neither this module nor the
legacy writer drifts from the one producer contract both ultimately read.

### Always the DATED endpoint, never the bare one

`lanes/_providers/nasa-firms.toml` declares two forward-shaped endpoints, `area-csv` (today's
rolling window) and `area-csv-dated` (one exact past day). Every request this strategy plans names
an exact already-settled UTC day (`start_date=day`), matching
`ingest/firms.py::fetch_active_fires`'s own dated branch and the legacy direct writer's identical
choice (`pipeline/direct/fire_detections/source.py`) -- never FIRMS' bare "now" window, which this
lane's forward cadence has no use for.

### `{api_key}` is never touched here

`nasa-firms.toml` declares `api_key_transport = "path"`: the reserved `{api_key}` path segment is
filled by the provider client alone, from `api_key_env` (`NASA_FIRMS_KEY`, named, never a value)
under the endpoint's `customer_host` opt-in (`foundation/lane_config/AGENTS.md` "Path templates";
`ingest/AGENTS.md` "Path-segment keys"). Every `area-csv-dated` send carries exactly the OTHER four
placeholders that template names -- `source`, `area`, `day_range`, `start_date` -- and the one
`data-availability` send per turn carries no parameter at all (its template names only
`{api_key}`); `split_parameters` routes them all into the path, so no send has a query string,
byte-for-byte the legacy producer's own URL shapes.

### Availability-aware coverage: one unit per settled day, its products read from FIRMS itself

This closes the scope limit earlier revisions documented here (a pinned three-product
`expected_units`), for the forward AND the gap-fill path alike. Each day is ONE `SourceRequest`
(the retry grain), and its `fetch`:

1. reads FIRMS' live `data_availability` table through the declared `data-availability` endpoint --
   ONCE per turn, shared by every day unit of that turn (`_TurnAvailability`, keyed on the turn's
   own budgeted client); a failed read is never cached, so the runner's per-unit retry ladder
   re-asks it;
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
  fails the unit, so on this `partial_day = "refuse"` lane `turn.py::_settle_answered` reports the
  day unwritten under that failure's own reason and nothing is written. An availability read that
  fails fails every unit that needed it the same way: every owed day held, nothing written, and the
  runner's own exit (75 when every sent unit ended `upstream_unavailable`; a parse fault is a
  per-day `strategy_error`). There is no fallback to a pinned constellation. A day the table names
  NO covering product for is a loud per-day `Unsettled` naming the candidates, never a quiet skip
  and never a governed absence.

### Why per-day units, not per-(day, product), and why not `SourceCoverageStrategy`

The contract's `SourceCoverageStrategy.source_unit_ids(lane, region)`
(`pipeline/runner/contract.py`) is ONE stable support set for every day, and `turn.py::_settle`
rejects any `Written.expected_unit_ids` that differs from it -- the wrong shape for coverage that
varies by era, so this strategy does not implement it and sets no unit identities (counts only, as
before). `plan_requests` is synchronous and runs before any send, so it cannot know which products
cover a day; planning one unit per CANDIDATE product would reserve seven units of the turn's
request cap per day for products never sent. One unit per day reserves one
(`budget.py::select_affordable_days`), while `BudgetedClient` still charges every real send -- the
availability read once per turn (one `request_key`, however often retried), each covering product
once -- and refuses a send past the cap before it leaves (`deferred_budget`, never a partial day).
A day unit's `source` path value is the literal `DAY_UNIT_SOURCE` marker: the unit's key for budget
reservation, never a URL anything sends. Day units are never checkpoint-eligible
(`complete_parameters` is empty): a replay client answers only the one request it holds
(`checkpoints.py::_ReplayClient`) and could never re-read availability, so a retained answer would
be a write that can never restore. The remaining G6 preconditions (gap-fill budget sizing against
the one-reservation-per-day estimate; a product new to FIRMS but absent from
`FIRMS_HISTORY_SOURCES` is never asked) are listed above under "Remaining G6 preconditions".

### The forward window re-asks absences, not already-written days

`lanes/fire-detections-direct-forward.toml`'s `absence_recheck_days = 5` is the "refetch window"
split back from the edge (`pipeline/runner/AGENTS.md` "Windows": `forward_window = [edge -
absence_recheck_days + 1, edge]`). Inside that window, `turn.py::_series_forward` owes only
`_owed_days()` (missing or incomplete) and re-asks `census.absent_days()` (governed absences, to
retract one once FIRMS starts reporting fires there) -- a day already written with every covering
product present is neither, so it is NOT re-fetched again by a later turn. This is a DELIBERATE,
narrower behaviour than the legacy hourly job, which blindly re-walks every day in its window
including already-complete ones (`docs/lanes/fire-detections.md` section 2: "Each turn refreshes
the bounded settled window... including already-complete direct-owned days"). The `refuse` nature's
narrower recheck loses nothing that mattered: the legacy citation's own worked example is a day that
STARTS empty and LATER acquires detections -- exactly the absence-retraction path
`census.absent_days()` re-asks every turn for the full 5-day window -- not a day that was already
complete gaining yet more detections after the fact (NRT's constellation members do not
retroactively revise an already-reported hotspot; MODIS/VIIRS SP reprocessing, which genuinely does
revise a day, is archive-only and out of this forward lane's realistic window).
`tests/lanes/fire_detections/
test_nasa_firms.py::test_an_initially_absent_day_is_re_asked_and_its_absence_retracted` pins the
retraction path this mechanism actually exercises.

### The 10,000-record cap, surfaced as an explicit UNWRITTEN day, never a silent partial

`docs/lanes/fire-detections.md` section 5.1 ("THE HEADLINE TRAP") documents
`INGEST_MAX_SOURCE_RECORDS` defaulting to 10,000 and the legacy ROLLING forward job silently
keeping only the newest 10,000 of a merged multi-day, multi-product tick -- a measured, real loss
(memory note `agri-firms-record-cap-drops-silently`; `docs/lanes/fire-detections.md:234-248`). This
strategy's request shape is narrower (one exact day, one send per covering product) than the job
that number was measured against, but the number itself is the one piece of upstream behaviour this
repo has ever measured against FIRMS' per-request CSV answer, so `settle()` treats ANY single
product-day response landing at or above that same threshold as a tripwire: such a response can
never be proven complete (a response sitting exactly at a known truncation point is
indistinguishable from one that was cut there), so the day is reported `Unsettled` -- visible in
the S5 report's `unwritten` list every turn -- rather than either (a) silently writing a truncated
sample as if it were the whole day, or (b) silently dropping the overflow the way the legacy
rolling job does. The next hourly turn re-asks it inside the 5-day `absence_recheck_days` window; a
day that keeps tripping the cap stays visibly `unsettled` rather than quietly wrong.

### Governed absence is unconditional here, unlike `drought.usdm_releases`' mirrored-past pattern

`settle()` does not need `DayContext.later_published_day`: a day where every product the
availability table names as covering it answered (no missing member, no cap hit) with zero total
rows is an immediate `Absent` -- directly mirroring the legacy adapter's own unconditional governed
absence (`pipeline/direct/fire_detections/adapter.py`: "all {products} requests succeeded... and
yielded zero accepted records"). USDM's 404 needed a mirrored-past proof because a 404 is ambiguous
("not yet" vs "never"); a FIRMS product answering its CSV endpoint with a header and zero rows is
not ambiguous in the same way -- it is a successful, already-settled empty answer.

### `Written.source_digest` is left `None` on purpose

Unlike `drought.usdm_releases`' `ingested_at` or `fire_perimeters.wfigs`'s `updated_at`, this
stream's rows carry no capture-time column at all (`warehouse/schemas/fire_detections.py`'s
`FIRE_DETECTIONS_SCHEMA`; the TOML's `[[streams]]` comment states this explicitly, so no
`volatile_columns` entry is needed either). The built row content is therefore deterministic given
the same detections, so the runner's own rows-table digest (`Written.source_digest`'s docstring:
"`None` lets the runner digest the day's rows instead") already does the right thing wherever this
strategy IS re-asked for a day it has an opinion on: an unchanged governed absence re-confirms with
no spurious write, and (per "The forward window re-asks absences" above) a day whose absence
retracts writes exactly once, with zero extra digest-exclusion code here.

### Identity and geometry defects refuse the whole day, from `rows()`, never a silent drop

Any unkeyable/invalid detection, any detection landing outside the requested exact day, or any
non-finite coordinate/FRP value raises `ValueError` from `rows()` -- the runner's own vocabulary
turns a `rows()` fault into that day's `strategy_error`, never the turn's
(`pipeline/runner/AGENTS.md`: "A strategy fault in settle/rows/derive is that day's strategy_error,
never the turn's"). This restates, rather than relaxes, the legacy writer's
`identity_defect=REFUSE_WHOLE_RELEASE` / `geometry_defect=REFUSE_WHOLE_RELEASE` policy.
