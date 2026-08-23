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
