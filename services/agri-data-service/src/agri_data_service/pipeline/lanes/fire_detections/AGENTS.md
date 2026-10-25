# `pipeline/lanes/fire_detections/` — the fire-detections config-lane strategy package

A NEW package (`s-fire-detections`, track `config_driven_ingestion_20260926`, Phase 4): `p4-extract`
moved nothing here, because fire-detections' legacy modules
(`pipeline/direct/fire_detections/**`) were never shared with a non-lane caller, unlike
`evacuation_zones`/`watersheds`/`burn_severity`/`drought`. Everything below is authored fresh beside
`pipeline/direct/fire_detections/**`, which stays the active legacy implementation
(`executor = "legacy"` in the shipped TOML) until a future G6-equivalent cut-over.

| module | what |
|---|---|
| `nasa_firms.py` | the strategy (`STRATEGY`): plans one unit per (settled day, VIIRS NRT product), fetches and parses the dated area CSV, settles (constellation-complete / capped / absent / written), and floor-aggregates to the registered cell-day grain |

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

## Why `expected_units` counts VIIRS NRT products, not grid cells

Unlike the `grid_refuse` conformance fixture (`tests/runner/fixtures/grid_refuse.py`), whose
`Written.expected_units` is the GRID's own cell count, this lane's coverage unit is "did this live
satellite's product answer for this day" -- there is no fixed cell population to compare against;
a settled day's true detection count is whatever FIRMS published, zero included. Counting
constellation members (3, always) rather than detections means a single down satellite's day is
never read as a quiet fire day. In practice that refusal happens one level UP from `settle()`:
`turn.py::_settle_answered` reports a day short of ANY unit directly under the failed unit's own
reason (e.g. `upstream_unavailable`) on a `partial_day = "refuse"` lane, without ever calling this
strategy's `settle()` -- see `nasa_firms.py`'s module docstring, "Two different mechanisms", and
its pinning test.

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

## Known gap, deliberately not closed here: no availability-aware gap-fill

The legacy source module (`pipeline/direct/fire_detections/source.py`) reads FIRMS'
`data_availability` table before every fetch and filters to the products that actually cover a day,
because its archive walker reaches into `VIIRS_*_SP`/`MODIS_SP` territory where coverage genuinely
varies by product and by era. This strategy skips that read entirely: its forward window
(`absence_recheck_days = 5` back from `today - publication_lag_days`) never leaves the three live
VIIRS NRT products' already-active coverage, so `expected_units` is safely pinned at 3. **This
makes `gap_fill_enabled = true` unsafe as written, for a quiet reason rather than a loud one** --
`ingest/firms.py` documents that a product asked for a day it does not cover answers with fewer
rows, never an error ("over-asking is safe and cheap"), so a walk back toward the TOML's `floor =
2000-11-01` would likely still settle and WRITE each ancient day, using only whichever of the three
NRT products happened to exist yet and silently omitting every `VIIRS_*_SP`/`MODIS_SP` detection
the legacy archive walker's availability-aware fan-out would have included. History served
under-counted, looking complete, is the real risk here, not a hard refusal. Porting
`fetch_product_availability`/`products_covering_span` into this strategy (or an equivalent
`SourceCoverageStrategy`) is the prerequisite for ever flipping `gap_fill_enabled` here; tracked at
the TOML's `gap_fill_enabled_at_gate = "G6"` line, not fixed in this slice.
