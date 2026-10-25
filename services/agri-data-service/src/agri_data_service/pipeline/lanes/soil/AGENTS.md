# `pipeline/lanes/soil/` — the config-lane soil strategy

`strategy = "soil.open_meteo_era5_land"` (S14) for `lanes/soil-era5-land-direct-forward.toml`, slice
`s-soil` of track `config_driven_ingestion_20260926`. Dark: the TOML ships `executor = "legacy"`.
Every behaviour is derived from `pipeline/direct/soil/**` (read-only; never imported, per
`pipeline/lanes/AGENTS.md`). The lane-level writeup is `docs/lanes/soil.md` §8.

| module | what |
|---|---|
| `open_meteo_era5_land.py` | `STRATEGY`: `plan_requests`, `fetch`, `settle`, `rows`, `probe_edge` |
| `archive.py` | the governed request query, the part key, the canonical body, the body parse |
| `support.py` | the lattice in `cell_key` order, 50-cell chunks, the probe pair, cell identities |
| `rows.py` | the three historical row shapes (plane, moisture lineage, temperature lane) |

## The day

One request unit per (day, 50-cell chunk): 32 units, `31 x 50 + 18 = 1,568` weighted calls. The
unit name is `<day>:cells-NNNN`, so sorting a day's responses by unit is chunk order.

`settle` gives ONE settlement per lane-day for all eight streams (the runner settles days, not
stream-days). Per variable it counts valued cells:

- every variable at 1,470 -> `Written(1470, 1470)`;
- any variable short (including one variable all-null while the rest are whole) ->
  `Written(1470, min count)`, which the runner turns into `refused_partial` on a `refuse` lane;
- any variable above 1,470 -> `SoilSettlementError` (the land mask moved; `strategy_error`);
- every variable at 0 -> `Absent` only when EVERY stream already publishes a later day with values
  (`DayContext.later_published_day`, the legacy `_mirrored_past_day` proof), else `Unsettled`.

**Deviation from legacy:** the legacy walk published each product independently, so a day whose VPD
had landed but whose temperature had not wrote VPD and refused temperature. Here a mixed day is held
whole. The probe already requires all eight variables at both probe cells before a day fans out, so
a mixed day is a frontier transient that the next fire re-asks.

A day at or below `ERA5_LAND_SNAPSHOT_LAST_DAY` raises in `settle` (the legacy
`refuse_immutable_day`); the TOML's `[days] floor = 2026-08-03` already keeps every window above it.

## The archive wire

`archive.py` restates the guards of `execution/open_meteo_lane.py` and `pipeline/direct/soil/source.py`
because a strategy may import neither: one location per requested point in order (`location_id`),
the native node within `0.05 + 1e-6` degrees of the asked centroid, GMT with zero offset, no two
points on one native node, the daily axis named exactly by ISO prefix, every value in its reviewed
range. A body that fails any of them raises `SoilArchiveBodyError` inside `fetch`; the runner's
`classify_fetch_error` maps it to `strategy`, the unit fails without retry, and the day is held as a
`strategy_error` -- never a partial write.

**The query** comes from `ingest/open_meteo.py::archive_daily_url`, the one governed builder, with
`base_url` passed so it never reads `OPEN_METEO_API_KEY`; only its query string is kept, in governed
order. The provider client sorts it before sending and adds the key itself.

**The canonical body** uses `foundation/canonical.canonical_json` (`ensure_ascii=False`), not
`_shared.digest.canonical_json_bytes`, which always escapes non-ASCII. The archive's `daily_units`
carry `m³/m³` and `°C`, and the legacy digest (`execution/contracts.canonical_json_bytes`) was taken
over raw UTF-8, so `_shared` would change every `response_sha256` and every lineage column built
from it. `SourceResponse.body` is that canonical document: its digest is the legacy
`response_sha256`, and a checkpoint replay re-canonicalizes it to the same bytes. The row
fingerprint (`rows.py::_direct_row_sha256`) does use `_shared.digest` with the legacy kwargs.

## Part keys

Rows record `selected_source_part_key` as the answered host and path plus the governed query in
governed order (`archive.part_key`), byte-equal to the legacy `soil_chunk_url` on the same host. The
runner's own `request_url` sorts the query, so it is not used verbatim. The host is the one the
response names, which is the free host (`ProviderResponse.request_url` is credential-free). The
legacy writer records whichever host it called (`archive_daily_url` defaults to the customer host
when a key is set), so the two agree only while the legacy lane runs keyless; see
`docs/lanes/soil.md` §8 "G6 compare".

## The support

`support.support_cells` takes the region manifest's `analysis-0p25` lattice (proven key-for-key
equal to the historical plan cells by `tests/lane_config/test_analysis_lattice.py`) and sorts it by
`cell_key`, the legacy support's `ORDER BY cell_key`. Chunks are cut in that order.

### Cell identities

Rows carry `cell_id` and `coverage_fraction` from `agri.spatial_cell`, which a strategy cannot read
(no session is handed to it, and Postgres is out of the serving path). They ship as package data,
`support_cells.json`, read lazily by `support.load_support_identity` and only in `rows`:

```json
{"schema": "soil-era5-land-support-identity-v1",
 "cells": [{"cell_key": "...", "cell_id": "<uuid>", "cell_latitude": 42.125, "cell_longitude": -124.875,
            "coverage_fraction": 1.0, "support_ordinal": 0}, ...]}
```

Produce it once from the legacy support read (`pipeline/direct/soil/support.py::_SUPPORT_CELLS_SQL`,
`WHERE grid_name = 'sentinel2-ndvi-0p25deg' ORDER BY cell_key`), `support_ordinal` = row position.
**Shipped 2026-10-25** (commit `7ebe29b9`): extracted read-only from production `agri.spatial_cell`
with the legacy SQL verbatim; generation script preserved at
`.omc/research/lane-audits/raw/soil-cells.mjs`. `parse_support_identity` refuses a document whose
ordinals do not follow Python's `cell_key` sort: a Postgres collation that disagreed would also have
chunked the legacy requests differently, so it must be loud, never reconciled. Two facts verified at
extraction time:

- **Ordinal collation agrees.** Postgres's `ORDER BY cell_key` order was checked equal to Python's
  code-unit sort over the same 1,568 keys, so `parse_support_identity`'s ordinal check accepts the
  shipped document without reconciliation.
- **`coverage_fraction` is 1.0 for all 1,568 cells, and that is production's real state, not an
  extraction artifact** -- a `GROUP BY coverage_fraction` on 2026-10-25 returned a single value. The
  1,470 `VALUE_CELL_COUNT` (`open_meteo_era5_land.py`) is unrelated to coverage: it comes from the
  Open-Meteo archive answering `null` for the 98 ocean/out-of-domain cells on an otherwise-complete
  day (`pipeline/direct/soil/support.py` ERA5_LAND_VALUE_CELL_COUNT comment), never from a cell's own
  coverage fraction being partial.

## The probe

Two centroids straddling the lattice envelope's centre (`support.probe_points`; the legacy
`probe_cells`), over the runner's 14-day window: 2 weighted calls. A day is valued when every
variable is valued at both. A typed fetch error propagates (the runner makes 429 `deferred`, 5xx
`unavailable`); a body that cannot be parsed returns `ProviderEdge(status="unavailable")`. Either way
the window is gated and the turn exits 0 (O-R3-1). `invalid` and `blind` are the runner's derivation
(`windows.py::effective_probe_status`), not the strategy's. `probe_edge` is handed no lane, so it
reads the pinned lattice from the active region (`load_region()`).

## Volatile columns

`rows` stamps `selected_source_release_retrieved_at` (moisture) and `selected_release_retrieved_at`
(temperature) from the day's newest chunk `retrieved_at`; the TOML declares exactly those, and
`tests/lanes/soil` proves the declaration equals the set of timestamp columns carrying the retrieval
instant. `source_snapshot_id`, the part keys and the row fingerprints are content, not capture time.
