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
