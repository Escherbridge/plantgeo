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
region envelope once; `capture --root DIR --area ID001 --apply` acquires one bounded slice of one
area, resumable across invocations and safe to re-run with a smaller `--page-size` after a 16 MiB
response refusal (each `CapturePage` records its own `page_size`, so a capture with mixed page sizes
across resumed invocations still validates). `prepare`, `verify-replay`, `validate`, `stage` and
`release` are a later slice's sequenced addition to this same `__main__.py`; until they land there
is no path from a local capture to anything served.

## No shim here

Unlike `drought/` and `burn_severity/`, this package renames nothing: there was no module to move.
It therefore contributes nothing to the wave-4 deletion list.
