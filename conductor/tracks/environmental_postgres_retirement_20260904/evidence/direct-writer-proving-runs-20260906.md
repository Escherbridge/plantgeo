---
type: evidence
---

# Direct-writer proving runs, 2026-09-06/07

Criterion 4 requires each of the shadow `*-direct-forward` writers to be proven before activation.
This records what each proving run actually did, measured, including one that published live.

Environment note that cost two failed attempts: **all of these writers need `INGEST_BBOX`**, which the
executor container carries and a local shell does not. Production value, read from
`plantgeo-job-executor`: `INGEST_BBOX=-125,42,-111,49`, `INGEST_MAX_SOURCE_RECORDS=50000`.

The two lanes handle its absence differently, and both are deliberate:

- `fire_perimeters` **raises** — `INGEST_BBOX is not configured, so there is no bounded extent this
  version could claim to cover`.
- `evacuation_zones` **skips the turn** with `outcome="bbox_unconfigured"`, because "this lane's
  coverage is bounded twice -- by Oregon's own statewide feed and by INGEST_BBOX -- so an unset bbox
  SKIPS the turn rather than widening the query, exactly as `run_evacuation_zones_ingestion_job` did".

## watersheds — PROVED, correct no-op

```json
{"accepted_basins": 9396, "rejected_basins": 0, "published": false, "state": "current",
 "detail": "version 2026-08-07 is later than the source watermark 2019-11-21, so this reference set
            is current (NHDPlus_HR WBDHU12 loaddate (direct fetch))"}
```

All 9,396 basins fetched from NHDPlus_HR with **no Postgres in the path**. `published: false` is the
right answer, not a failure: the published set already leads the source watermark.

## evacuation-zones — PROVED, and it published a live version that CORRECTED the layer

```json
{"day": "2026-09-07", "outcome": "written", "published": true, "zones": 116,
 "rows_across_write": 116, "parts": 1, "written_bytes": 480770,
 "detail": "derived z9 116 rows in 1 part(s), z5 116 rows in 1 part(s), z0 116 rows in 1 part(s)"}
```

**This was a `--max-days 1` proving run and it published to production.** These writers carry no
`--dry-run`. Version history at `layer=evacuation-zones/kind=observed/zoom=13/`, read from each
`_complete.json`:

| version | row_count | written_at |
|---|---|---|
| 2026-08-24 | 677 | 2026-08-24T05:29:50Z |
| 2026-09-02 | 718 | 2026-09-04T02:22:04Z |
| **2026-09-07** | **116** | 2026-09-07T00:10:52Z |

A 6x drop is exactly the shape a parity check exists to catch, so it was checked against the source
before anything else. **116 is correct.** Counted live at the feed:

```
.../Fire_Evacuation_Areas_Public/FeatureServer/0/query?where=1=1&returnCountOnly=true
  -> {"count":116}
same query clipped to -125,42,-111,49
  -> {"count":116}
```

The reason 718 was wrong is already documented at `ingest/evacuation_zones.py:68-72`: the hosted
view's definitionQuery "keeps an automated-editor row visible **only while the upstream integration
keeps re-confirming it**, so a query answers with the CURRENT statewide evacuation picture rather than
a static zone catalogue." The Postgres pair could never retire a zone — supersession needs
`observed_at > version_valid_from` and both are the same immutable `created_date` — so 677 and 718
were accumulations of long-closed evacuation areas, not the live picture. This is the direct writer
seeing what the Postgres chain structurally could not.

Confirmed visible to the reader: `/api/v1/parquet/coverage` now lists `2026-09-07` for
evacuation-zones at every rung (z0/z5/z9/z13).

**Lesson for the remaining proving runs:** a `--max-days 1` run on a `static_lookup` lane is a
PUBLICATION, not a rehearsal. Check the source count first, then run.

## fire-perimeters — BLOCKED, and it is an activation blocker, not a flake

```json
{"error": "FirePerimeterGeometryError: WFIGS perimeter '2026-IDBOD-265460' carries an invalid
           geometry, which geo_features_sync_geom refuses with SQLSTATE 22023 -- so PostgreSQL never
           held this perimeter either. The whole snapshot is refused rather than published without it",
 "status": "failed"}
```

The refusal is deliberate and its parity argument is sound as far as it goes: PostgreSQL's
`geo_features_sync_geom` would also have refused this row. But the *blast radius* differs — Postgres
refused **one row** and kept the rest; this writer refuses **the whole snapshot**. So while any single
upstream WFIGS perimeter is invalid, the lane cannot publish at all, and one is invalid right now.

This must be resolved before `fire-perimeters-direct-forward` is activated, and it is an owner-visible
design question rather than a bug to fix silently: does an invalid upstream perimeter drop that
perimeter (with the drop recorded in the completion marker, as `watersheds` does with
`rejected_basins`), or does it hold the whole snapshot? The `watersheds` writer already sets the
precedent for the first answer.

## Still unproven

`burn_severity` and `sensors` direct writers. `sensors` additionally carries the known z13-stranding
defect: 25 of its 39 bucket days hold part files at z13 with no coarse rungs, from a tier derivation
naming `station_longitude`/`station_latitude` that its base table does not carry.
