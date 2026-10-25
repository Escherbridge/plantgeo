---
type: lane-contract
slug: drought
horizon: none
---

# `drought` lane

Written 2026-08-22, **after** the other eleven — and that is the point of this first paragraph.
`drought` is **stream S2** in [`conductor/RUNBOOK.md`](../../conductor/RUNBOOK.md) §0.24.1, a
first-class stream, but it is **not one of the eleven `geo.layers` slugs**. A wave-2 fan-out scoped
to "the eleven lanes" skipped it entirely, and nobody noticed until the warehouse was audited
against production relation sizes. **If you are enumerating work from `geo.layers`, you are
missing this stream and `forecast-observation`.**

## 1. Source system

USDM (U.S. Drought Monitor, droughtmonitor.unl.edu), published **weekly** — released Thursdays for
the preceding Tuesday (`services/agri-data-service/src/agri_data_service/ingest/usdm.py:54`).
Keyless. Backfill can walk to `USDM_ARCHIVE_START = 2000-01-04`
(`ingest/usdm_history.py:42`), though production history in practice begins ~2022-08.

Rows land in **`geo.drought_areas`**, not `geo.features` — this lane does not use the feature
table or the Type-2 geometry dimension that ten of the eleven layer lanes read.

## 2. Grain

**`(valid_date, dm_category)`** — the exact pair the table's own unique index enforces
(`drizzle/0007_governed_environmental_ingestion.sql:20`). One polygon per USDM drought class
(D0–D4) per weekly release.

## 3. Measured size — the canonical "never size geometry by row count" case

**1,045 rows, 500 MB** (measured against production 2026-08-22) — roughly **500 KB per row**. This
is the example the runbook cites whenever it warns against estimating a geometry relation from its
row count, and it is why the exporter bounds each `part-N` file by **measured `table.nbytes`**
rather than a fixed row count.

The row count was **995** a week earlier (RUNBOOK `:1061`), which independently confirms the weekly
producer is alive.

## 4. Two facts confirmed at the database, not assumed

- **`valid_date` is `varchar(10)`, not a date type** — but it carries
  `CHECK ("valid_date" ~ '^\d{4}-\d{2}-\d{2}$')`
  (`drizzle/0007_governed_environmental_ingestion.sql:11,17`), so ISO-8601 is a schema guarantee
  rather than a convention. The export converts it to a real `date32`; carrying a stringly-typed
  date into the warehouse would push the ambiguity onto every future reader. Conversion is
  deliberately **unguarded** — a value that fails to parse must abort the export loudly.
- **SRID is 4326**, confirmed twice: the column declares `geometry(MULTIPOLYGON,4326)` (`:13`) and
  the write path independently stamps `ST_SetSRID(..., 4326)`
  (`sql/ingest/store_drought_area.sql:103`). WKB carries no SRID header, so this is an out-of-band
  fact a reader must be told.

## 5. Forecast recommendation

**`horizon: none`.** A USDM drought class is a **published assessment by an analyst panel**, not a
measured physical process. Projecting it forward would be forecasting an editorial decision and
presenting it as a measurement — the failure mode the lane standard exists to prevent. Ship no
`method/monte_carlo/drought.py`.

## 6. Absence

A release week the source never published is a **governed absence** with evidence, never an empty
partition. `read_drought_release` returning zero rows raises `EmptyPartitionError` from the writer
by design; the caller records the absence rather than working around the refusal.

## 7. Known gaps

- **No `docs/lanes/drought.md` existed until now**, so the exporter was built from the repo with
  `path:line` citations rather than from a contract. This document is the retrofit.
- **History extent in production is unverified.** ~2022-08 is inferred from the runbook, not
  measured. Confirm with a `min(valid_date)` before declaring a history floor to gap detection —
  a wrong floor invents years of phantom gap-days.
- `dm_category` is expected to be D0–D4 (0–4); **the repo does not constrain it**, so a reader
  should not assume the range without checking.

## 8. Config-lane strategy (`s-drought`, track `config_driven_ingestion_20260926`)

A dark `release_series` TOML and strategy now sit beside the legacy direct writer, built against the
Phase 4 "frozen" runner contract:

- **Files.** `services/agri-data-service/lanes/drought-direct-forward.toml` (new, `executor =
  "legacy"`, `enabled = true`); `pipeline/lanes/drought/usdm_releases.py` (strategy key
  `drought.usdm_releases`; `usdm.py` is a **reserved** moved source module with no `STRATEGY`, so the
  strategy lives in this distinct leaf); `pipeline/lanes/drought/geometry.py` (the base-rung DuckDB
  spatial repair chain, duplicated — not imported — from `pipeline/direct/drought/support.py`, since
  this package may never import `pipeline/direct/**`).
- **404 is `unsettled`, never `absent`, in the runner's own vocabulary.** USDM's documented "not
  published yet" 404 is caught inside `fetch()` itself and returned as an ordinary successful
  `SourceResponse` (`payload=None`) — it never reaches `pipeline/runner/fetch.py::classify_fetch_error`,
  which has no 404 case and would otherwise fold it into the generic `"strategy_error"` bucket (an
  upstream/code-fault word, wrong for an entirely expected weekly state). `settle()` then applies the
  contract's own vocabulary: `Unsettled(reason="unsettled")` when nothing proves USDM's cadence moved
  past this Tuesday, or `Absent(...)` — the release_series "mirrored-past" pattern, read via the
  contract's `DayContext.later_published_day` — when a later Tuesday is already published. Either way
  the exit code stays 0 (`pipeline/runner/exits.py`: unwritten days are a report fact, never an exit
  code).
- **Cron narrowed to the release window.** The legacy lane fires hourly every day
  (`execution/lane_specs.py` `"45 * * * *"`); the new TOML keeps the hourly `:45` minute but narrows
  the day-of-week field to Thursday–Saturday UTC (`"45 * * * 4,5,6"`), matching USDM's Thursday
  release for the preceding Tuesday plus the slack `publication_lag_days = 4` already budgets
  (Tuesday + 4 = Saturday). Trade-off stated rather than hidden: a release delayed past Saturday's
  last fire (23:45 UTC) waits until the following Thursday's first fire (00:45 UTC) — about **4 days
  1 hour** later, touching 5 calendar dates (Sun, Mon, Tue, Wed, Thu), **not** "up to six days" as an
  earlier draft of this document overstated (corrected: ring review, s-drought fix batch). The
  alternative weighed and declined — running all week, the legacy cron's shape — costs **zero extra
  HTTP requests** in the common case: a no-op forward turn (nothing newly owed) never sends, and a
  written Tuesday is never re-settled by a later turn, so all-week-hourly and narrowed-hourly make the
  identical few sends once a release actually appears. All-week's only real win is catching a delayed
  release within the hour instead of waiting for Thursday; narrowing's only real win is 96 fewer no-op
  scheduler invocations a week (168 → 72), not fewer HTTP sends. Accepted given the registration's own
  measured 209/209 continuous weekly releases (2022-08-09..2026-08-18) and because
  `absence_recheck_days = 56` (matching the legacy writer's own `DROUGHT_ABSENCE_RECHECK_WEEKS = 8`)
  keeps re-examining an unsettled Tuesday across several release windows, not only the one it first
  came up owed in.
- **Known gap: USDM's per-release resource is a dated URL PATH segment**
  (`ingest/usdm.py::usdm_source_url`), never a query parameter. `pipeline/runner/binding.py::
  ConfigProviderClient` and `ingest/provider_client.py::provider_endpoint_request` build every
  request URL as a provider's declared (fixed) `path` plus a `?query` string — there is no
  declared-endpoint shape that can express a path segment varying per request. This is the identical
  gap `lanes/_providers/nasa-firms.toml`'s own TODO names for FIRMS's path-segment API key. The
  strategy is otherwise complete and tested (`tests/lanes/drought/test_usdm_releases.py`), but the
  TOML cannot move to `executor = "config"` until a provider-client path-segment transport is added —
  out of this slice's owned files (`pipeline/runner/binding.py`, `ingest/provider_client.py` are
  framework files no swarm lane may edit). Reported to `i-coordinator`.
- **Known gap #2 (ring review, s-drought fix batch): G6 compare cannot run against this lane
  either.** The strategy's `_source_digest` excludes `ingested_at` so a recheck of unchanged content
  never reads as a revision inside its own `settle()` — but that is a *different* digest over a
  *different* input than G6's actual compare path. `pipeline/runner/turn.py::_Turn._compare` diffs
  `reader.canonical_digest(stream, built)` against `reader.canonical_digest(stream, published)`, and
  `canonical_digest` (`reader.py`) hashes the **whole conformed table**
  (`digests.py::table_digest`, Arrow IPC bytes, every column) with no exclusion mechanism. `ingested_at`
  is a `DROUGHT_SCHEMA` column `rows()` stamps with the fetch's own `retrieved_at`, so a freshly built
  table deltas against *any* already-published day's table on that column alone — a false delta, on
  every already-settled day, every time G6 compare runs. This is not drought-specific: any lane whose
  schema carries a retrieval-instant column hits the identical false delta the moment it reaches G6
  compare (the `direct:`-row convention's general shape). The fix is outside this slice's owned files
  (`pipeline/runner/turn.py`, `reader.py`, `digests.py`): `_compare`/`canonical_digest` need to become
  volatile-column-aware before G6 can run here. Recorded as a G6 prerequisite with `i-coordinator`.
- **No probe_edge needed.** `lanes/_providers/usdm.toml` declares `weighted = false`; S19's probe_edge
  rule (`foundation/lane_config/models.py::lane_requires_probe_edge`) binds only a `refuse` lane on a
  *weighted* provider, so drought is exempt despite its forward cron firing more than once a day.
