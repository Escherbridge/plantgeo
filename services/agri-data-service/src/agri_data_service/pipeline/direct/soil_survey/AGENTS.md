# `pipeline/direct/soil_survey` — the soil-survey layer's source protocol and acquisition CLI

This package holds a live-region contract/binding pair (`source_protocol.py`, `ssurgo.py`) **and**,
since the 2026-09-27 SSURGO native-geometry port, an offline capture CLI (`source.py`, `capture.py`,
`__main__.py`) that talks to the same USDA source by a different, explicit path. **Neither is a
writer**: the CLI produces local, unpublished receipts an operator stages and admits by hand, it is
not reachable through the live protocol above, and it is not registered as a scheduled lane (see
"Operational entry point" below). This package is deliberately separate from `pipeline/direct/
soil/`, which is a different layer despite the similar name: that one is the ERA5-Land soil *field*
(moisture, temperature, VPD) writer fed by Open-Meteo. `soil-survey` is USDA SSURGO map-unit
delineations — reference geometry with a version stamp (`warehouse/schemas/soil_survey.py`,
`planes/soil_survey.py`, `docs/lanes/soil-survey.md`). Conflating the two is the single easiest
mistake to make in this tree.

## The source protocol

`source_protocol.py` states what any region's soil-survey source must declare (`source_slug`,
`coverage`), how its own change watermark is read (`source_vintage_watermark`) and how a release is
pulled (`fetch_release`) — `federation.md` §2, `layer-lanes.md` §1b.

The shape is built around `layer-lanes.md` §1a's `static_lookup` nature rather than around a day:

- `fetch_release` takes **no day**. A release is the whole published survey at one vintage, not a
  day's observations, so a per-day pull would be a category error the partition layout happily
  hides.
- `SoilSurveyRelease.vintage_day` is a **version stamp**, and the protocol's docstring says so at
  the field, because a partition dated at the run date is exactly the defect §1a retracts an
  earlier ruling over.
- `source_vintage_watermark` must return a **change event**, never a poll clock. A column a re-fetch
  of unchanged ground advances launders polling into the version stamp.

## Why the pull raises

`ssurgo.py` declares the coverage claim the PNW manifest's `soil-survey` → `ssurgo` binding needs,
and raises `SsurgoPullRetiredError` from both pull methods. The SSURGO ingest module was retired in
the 2026-09 Postgres cleanup and no source-direct lane replaced it;
`pipeline/parquet/lane_registry.py` already refuses this lane's retired database watermark with
"publish SSURGO through its source-direct Parquet lane".

Raising beats the two alternatives. Returning an empty release would be a *claim* that SSURGO
published nothing, which this tree has not observed — the distinction `layer-lanes.md` §1a draws
between "current" and "not looked at". Omitting the binding entirely would leave the region check
in `foundation/region/bindings.py` unable to see any claim for `ssurgo`, so it would pass vacuously
for the one layer whose absence most needs to be visible.

Turning that absence into a governed serving answer (`absence_reason = source_unbound_for_region`
through the availability index, slider catalogue, legends and agent tools) is **wave-4 work**, not
this package's. Nothing calls these methods today, so nothing changed behaviourally.

`ssurgo.py`'s own docstring points here for why its offline capture-and-candidate CLI (`__main__.py`,
below) is not this protocol's `fetch_release` either: that CLI acquires the same USDA source through
an explicit, separate path, produces local unpublished receipts an operator stages and admits by
hand, and is registered as no lane -- it changes nothing about why the live pull above still raises.

## The acquisition CLI (ported 2026-09-27, slice S1)

`source.py` builds the four SSURGO T-SQL queries (`sql/pipeline/ssurgo_{summary,keys,page,
area_inventory}.sql`) and reads USDA SDA's `JSON+COLUMNNAME` tables; `capture.py` runs one bounded,
resumable, single-owner acquisition of one survey area at a time, writing every raw response as an
immutable, content-addressed local object. Both were ported from `archive/
freshness-integrated-candidate-20260914`'s `pipeline/direct/soil_survey/{source,capture}.py`; see
the disposition record in `.omc/research/merge-20260927/soil-survey-port-plan.md` for what changed
and why. The receipt models these two modules build (`Blob`, `AreaSummary`, `CapturePage`,
`AreaCapture`, plus the new `AreaInventory`) now live in `foundation/soil_survey/receipts.py`, not
in this package, because a future `pipeline/lanes/**` strategy must never import `pipeline/
direct/**` (`spec.md` l.307) and `planes/`, `interface/http/` and `pipeline/validation/` all need
these types too.

**Freeze 1** (in effect before this port's first push): `receipts.py`'s names and fields,
`capture_area`, `save_blob`, `read_blob`, `validate_page`, the four query builders in `source.py`,
and this package's CLI verbs are frozen. A later slice may *add* to this package (new verbs, new
receipt types) but must not rename or reshape any of the above without a second grilling round.

## Source and identity

USDA SDA's [Web Service Help](https://sdmdataaccess.nrcs.usda.gov/WebServiceHelp.aspx), read
2026-09-13 during the archived investigation this port carries forward (`archive/
freshness-integrated-candidate-20260914`), documents the POST query service, `JSON+COLUMNNAME`, and
a 100,000-row-per-**query** ceiling. That ceiling bounds one paginated query's response, not
`AreaSummary.count` (`foundation/soil_survey/receipts.py`'s `MAX_AREA_DELINEATIONS`): the number
that field holds comes from a one-row `COUNT(p.mupolygonkey)` census (`ssurgo_summary.sql`), so a
single area can legitimately report a count far larger than any one query's row cap (P1-03). Query
semantics and native geometry follow the [Query Help](https://sdmdataaccess.nrcs.usda.gov/
QueryHelp.aspx) and the retired USDA-era Postgres ingest's full-polygon projection. SSURGO is a
static survey product, not a daily observation: `sacatalog.saverest` is retained exactly in source
receipts, and `checked_at` is an independent UTC acquisition clock, never treated as a substitute
for the source's own vintage.

The source grain is `mupolygonkey`; `mukey` describes a map unit and repeats across delineations.
Fresh source metadata read during the same 2026-09-13 investigation confirmed both are SQL Server
`int`, not textual orderings, so keyset paging (`source.py::page_query`) compares the native integer
column directly. A full joined keyset-and-geometry query timed out even at one row in a bounded
source probe; separating key inventory (`ssurgo_keys.sql`) from the full-polygon payload
(`ssurgo_page.sql`) is why each `CapturePage` records two responses and both query hashes -- the
key-inventory call names exactly which native keys the payload call must return, and `capture.py`
refuses the page if the two disagree.

## Bounds, resume and missingness

One capture invocation owns exactly one explicit area. Defaults are eight pages of 500 rows
(`capture.py::DEFAULT_MAX_PAGES`, `DEFAULT_PAGE_SIZE`) and 120 seconds
(`DEFAULT_TIME_BUDGET_SECONDS`); hard ceilings are 64 pages, 500 rows/page, 600 seconds, and 16 MiB
per decoded response (`foundation/soil_survey/receipts.py`'s `MAX_CAPTURE_PAGES`, `MAX_PAGE_ROWS`,
`MAX_CAPTURE_SECONDS`, `MAX_OBJECT_BYTES`). These are conservative local resource limits chosen for
this machine and this process, not claims about USDA's own throughput. One process holds the
existing `filelock` OS lock for an area's journal; process exit releases it, checkpoint replacement
is atomic, and raw response files are immutable by SHA-256. Failed or time-limited work resumes
after its last durable native key.

A fresh census on resume must equal the opening count and exact vintage, or the resume is refused
(`capture.py::_capture_area`, "source watermark changed"). Opening and closing source counts and
vintages must match, every source row must carry the same area/vintage, and native keys must
strictly increase with no duplicates. A short or empty page before the census count is reached, a
transport error, a malformed response, or an unknown area is a refusal. An existing area with zero
source polygons can retain its own dated census receipt -- **no generic empty response creates a
governed absence.** SDA's own `{}` answer is always refused as a distinct `SoilSurveyError`
("SDA returned no rows"), never silently read as completion or as a governed zero, whether it
arrives on the very first call, a resumed key page, or mid-capture between otherwise-successful
pages. Same-count/same-vintage geometry-only upstream edits during a capture cannot be ruled out by
these censuses (R7); admission still requires an independent native-ID/geometry comparison and an
accepted mutable-source consistency policy, neither of which this slice implements.

## Literal substitution is still the safety boundary

All four T-SQL files in `sql/pipeline/` use `str.format`, not bind parameters: there is no
bind-parameter path into USDA SDA's POST endpoint, so validated literal substitution is the whole
safety boundary. It rests on three checks, all enforced before formatting in `source.py` and
`foundation/soil_survey/receipts.py`:

- `require_area` validates every area symbol against `^[A-Z]{2}[0-9]{3}$` before it reaches a
  template;
- every native key (a resume cursor or a polygon-payload key) is checked ASCII-decimal with at
  most `MAX_NATIVE_KEY_DIGITS` (18) digits;
- area-inventory envelope ordinates are written with `repr()` of Python floats already checked
  `math.isfinite` and within the WGS84 range, so they can only ever render as a plain decimal
  literal.

Each of the four T-SQL files' own header says so, quoting this section by name.

**Query bodies are frozen once a capture exists (F7).** `source.py::query_body()` strips every
full `--` comment line AND every blank line (P1-04) from a loaded `.sql` file, then `rstrip()`s each
surviving line, before it is formatted, sent to SDA, or hashed into a capture receipt's
`query_sha256`. This means the `-- Purpose:`/`-- Loaded by:` header and the clause-by-clause
walkthrough (`sql/AGENTS.md`, "Documentation standard") can be rewritten at any time -- rephrased,
extended, reflowed across more or fewer lines, or padded with a blank separator line -- without
invalidating a single existing receipt — only the four bodies below the header are ever frozen, and
only once a real capture has hashed one. A body change is a deliberate, reviewed test edit, not an
incidental one.

**Region scope, not a literal list (F1, F9, NFR-5, P1-01/SEC-1).** The `areas` command runs
`ssurgo_area_inventory.sql` against the *Region envelope* (`foundation/region/load_region().
envelope`), not the client's narrower `default_camera_envelope`, and this is the **only** default:
`--bbox` accepts no environment fallback (an inherited `INGEST_BBOX`, set on
`plantgeo-job-executor`, would otherwise silently narrow the census to the camera crop). An explicit
`--bbox` that differs from the region's own envelope is refused unless `--allow-non-region-scope`
is also given. The written `AreaInventory` receipt records the envelope and region slug it actually
used, and refuses to overwrite an existing `areas.json` census that used a different one. Every
later `capture --area` invocation checks its area against that receipt when one exists, and is
refused if the area was never censused -- area symbols are meant to come from this receipt, never
from a literal hard-coded list.

**Operational entry point.** `python -m agri_data_service.pipeline.direct.soil_survey`. Every
command is dry-run unless `--apply` is present and neither reports `serving_published=true`; the
dry-run report for `areas` also echoes the resolved bbox, region and override flag, so a
misconfigured `--bbox` is visible before any network call. `areas --root DIR --apply` censuses the
region envelope in a sequential tile grid (see "Census tiling" below), never one untiled call;
`capture --root DIR --area ID001 --apply` acquires one bounded slice of one area, resumable across
invocations and safe to re-run with a smaller `--page-size` after a 16 MiB response refusal (each
`CapturePage` records its own `page_size`, so a capture with mixed page sizes across resumed
invocations still validates). `prepare`, `verify-replay`, `validate`, `stage` and `release` are a
later slice's sequenced addition to this same `__main__.py`; until they land there is no path from a
local capture to anything served.

## Census tiling

**Why.** The Go-1 pilot (`.omc/research/soil-survey-pilot-20260927/GO-1-REPORT.md`, Anomaly 1) ran
`areas --apply` over the full pnw Region envelope (`[-126, 41, -110, 50]`, ~144 sq deg) and hit
`httpx.ReadTimeout` 3/3, at 31.5 s, 31.5 s and 32.0 s -- every attempt against `source.py::fetch`'s
then-hardcoded 30 s timeout. The identical query shape (`ssurgo_area_inventory.sql`'s
`sapolygon.STIntersects`), at a ~0.04 sq deg diagnostic bbox around Boise, ID, answered in 3.7 s.
This is a scope-size-dependent server-side cost, not a rate limit or an outage: no 429 or 5xx was
ever observed, only a hard client-side socket-read timeout.

**Fix.** `__main__.py::_areas` no longer sends one `area_inventory_query` over the whole resolved
envelope. It splits that envelope into a row-major grid of tiles (`_tile_grid`, default edge
`DEFAULT_CENSUS_TILE_DEGREES = 2.0`, overridable with `--census-tile-degrees` and validated only
`> 0` and finite, `_validate_census_tile_degrees`), then queries each tile's own `area_inventory_
query` SEQUENTIALLY -- one request at a time, the same politeness this CLI already keeps toward SDA
everywhere else -- and unions the rows across tiles by area symbol. An area whose polygon spans a
tile boundary can legitimately answer from more than one tile; the union keeps one entry per symbol
and refuses outright (`SoilSurveyError`, "conflicting vintages across tiles") if two tiles ever
disagree on that area's `saverest`, rather than silently picking one. The saved `AreaInventory.
envelope` still records the **whole** resolved envelope, never a single tile -- tiling is an
acquisition-strategy detail, not a change to what scope was actually censused.

`--census-tile-degrees` has no upper bound (revised from this fix's first push, review finding 2):
`_axis_edges` (below) already collapses a tile edge at or past the envelope's own longer dimension
into a single untiled band on its own, so an explicit `<= span` check added nothing and instead
refused every small `--bbox` -- including the Go-1 pilot's own ~0.4 deg diagnostic bbox -- under the
default 2.0 deg edge, since 2.0 exceeds a bbox that small. `_axis_edges` itself computes each axis's
band count from `math.ceil((high - low) / step)`, not from accumulating `cursor += step` in a loop:
the accumulation could leave `cursor` a hair below `high` from float rounding even when the division
was exact, adding a spurious hairline-wide extra band at the high edge (review finding 1) -- one real
enough to send a near-empty `STIntersects` polygon literal that SQL Server can refuse as invalid.

A tile's own `STIntersects` call legitimately answers "zero areas here" for a rectangular grid over
an irregular envelope, and SDA spells that with its own bare `{}`, which `source.py::table_rows`
refuses as a `SoilSurveyError` ("SDA returned no rows") by design (F7, this file's own "Bounds,
resume and missingness"). `_areas` catches exactly that one message per tile and treats it as zero
rows for that tile, letting the census continue -- the live-run defect this fix's own re-run
surfaced: an unhandled instance of that same refusal, with no per-tile catch around it, discarded an
entire 40-tile census at tile 33 of 40, which had zero intersecting survey areas.

`source.py::fetch`'s socket-read timeout is now a keyword-only parameter
(`DEFAULT_FETCH_TIMEOUT_SECONDS = 30`, unchanged default) instead of the old hardcoded literal.
`capture.py`'s three call sites (`_summary`, the key-inventory page, the polygon-payload page) all
omit it and keep the exact 30 s behaviour a resumable capture page already relied on. Only the
`areas` verb passes a caller-supplied value, via `--request-timeout-seconds` (default 30 s, validated
`> 0` and `<= MAX_REQUEST_TIMEOUT_SECONDS = 120`, `_validate_request_timeout_seconds`) -- a one-time
offline census is not latency-sensitive the way a resumable capture page is, so it is the one call
site allowed to ask for more time per request.

`AreaInventory.response` stays exactly one `Blob` (Freeze 1; `receipts.py` is untouched by this
fix). With N tile queries there is no single raw payload to point that field at, so it points at a
small JSON manifest -- each tile's own bbox, its own separately-saved, independently
checksum-verifiable blob (`save_blob` still runs once per tile, so every raw tile response is still
an immutable local object), AND its own `query_sha256` -- rather than duplicating the tiles' bytes
into it a second time. `AreaInventory.query_sha256` commits to `tile_degrees` plus every tile's OWN
query digest, in tile order (review finding 3, correcting this fix's first push): committing only to
the tile bboxes let a changed `ssurgo_area_inventory.sql` body produce the identical top-level digest
as the unchanged query, silently defeating F7's "query bodies are frozen once a capture exists" for
this one receipt -- an auditor could not tell the census ran different SQL. Per-tile digests, combined
with `area_inventory_query` being frozen and deterministic per bbox (F7), fully reconstruct every
query this census actually ran, including which body produced each tile's rows. The `areas --apply`
report also gains `tiles_queried` (the grid's own tile count) and `max_tile_seconds` (the slowest
single tile's own wall time), so an operator can see, from the report alone, whether a chosen
`--census-tile-degrees` is still generous enough relative to SDA's own response times without
re-reading the pilot's numbers.

**Measured, not assumed (review finding 5).** The default's own live re-run of the full 16x9 deg pnw
envelope at 2.0 deg tiles reached 33 of 40 tiles (32 non-empty + 1 legitimately empty) before the
live-run defect above aborted it: per-tile latency was 15.4 s median, 23.7 s max, 11.3 s min, zero
timeouts and zero 429/5xx. That is the evidence behind `DEFAULT_CENSUS_TILE_DEGREES = 2.0` today --
the prior comment's "well under the diagnostic bbox's 3.7 s" claim compared a 2x2 deg tile (~100x the
diagnostic bbox's own ~0.04 sq deg) against that smaller bbox's own latency with no measurement of
the tile size actually shipped, and was corrected rather than repeated.

## No shim here

Unlike `drought/` and `burn_severity/`, this package renames nothing: there was no module to move.
It therefore contributes nothing to the wave-4 deletion list.
