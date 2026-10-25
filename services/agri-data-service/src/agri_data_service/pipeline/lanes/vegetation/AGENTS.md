# `pipeline/lanes/vegetation/` — the vegetation (Sentinel-2 NDVI) layer package

Built by the `s-vegetation` swarm slice (track `config_driven_ingestion_20260926`, spec §7 vegetation
rows). Both lanes ship DARK: `executor = "legacy"`, so the live `pipeline/direct/vegetation` writer
and the shadow `execution/vegetation_partition_promotion.py` keep their paths unchanged. Source facts:
`docs/lanes/vegetation.md` (§8 is this package).

| module | what |
|---|---|
| `sentinel2_ndvi.py` | the `vegetation.sentinel2_ndvi` ingest `STRATEGY` behind `lanes/vegetation-sentinel2-ndvi-direct-forward.toml` |
| `governed_plane_promotion.py` | the `vegetation.governed_plane_promotion` transform `STRATEGY` behind the DISABLED `lanes/vegetation-ndvi-governed-plane-promotion.toml` |
| `support.py` | the 1,568-cell support: pinned `agri.spatial_cell` identities, checked against the region's `analysis-0p25` lattice |

Imports: `foundation`, `ingest`, `warehouse`, `pipeline/runner/contract.py`, `pipeline/lanes/_shared`
and this package only. Never `pipeline/direct/**` (behaviour is re-derived, cited, never imported)
and never `execution` (the promotion verb lives there; its facts are restated as constants and the
lane's tests cross-check them).

## The partial-day policy (derived from the legacy forward, never guessed)

`partial_day = "refuse"`, and a NDVI day is never partial. Sentinel-2 is sparse by construction: a
settled day filling 40 of 1,568 cells is an ordinary real day, not a short fetch
(`pipeline/direct/vegetation/source.py` module docstring). So `settle` states
`Written(expected_units = present_units = cells filled)`; it never claims the 1,568-cell support as
the expectation, which would make every real day `refused_partial`. Per day:

| legacy behaviour | where | config outcome |
|---|---|---|
| any filled cell > 0: publish the whole day | `direct/vegetation/source.py::VegetationDaySource.is_governed_absence` (126), `adapter.py::__call__` (116 else-branch) | `Written`, `source_digest` = the order-free record digest |
| zero cells, a LATER day published with values: governed absence | `adapter.py` (116-118), `forward.py::_mirrored_past_day` (629-632) | `Absent`, proof = `DayContext.later_published_day` (same rule, same sentence) |
| zero cells, nothing later published: refuse, no retry, next turn | `adapter.py` (118-127), `forward.py` (454-462, `source_unsettled`) | `Unsettled("unsettled")` |
| fetch truncated / off-lattice cellKey / malformed record field | `source.py` (290, 309, `_required_*`), retried by `forward.py` (446-450) then raised at 489 (whole turn fails) | that day's lean `strategy_error`; the other days still write (DEVIATION, deliberate) |
| one unreadable scene | skipped inside `ingest/vegetation.py::collect_ndvi_grid_records` | unchanged: the same function is called |

Clearest-record selection (`_clearest`) restates `source.py::_select_clearest`: lowest cloud cover
wins, a reported `0.0` is the clearest, an absent value ranks as 100.

## Probe: the newest-scene day is the edge (S19)

`probe_edge` sends ONE governed request through the handed `ProviderClient` (`earth-search`
`search`, keyless: no key, no `optional_api_key`, nothing to opt into): the legacy fetch's own item
search (`ingest/vegetation.py::scene_search_url`'s collection, bbox and cloud filter) over the probe
window, `limit=1`, sorted `-properties.datetime`. The newest matching scene's UTC day is the edge,
and EVERY window day up to it is reported valued, scene or not: for a STAC catalogue a day behind
the newest scene is answerable (data, or a settled zero-cell answer that `settle` governs with the
legacy proof). Reporting only scene-bearing days would hold every scene-less day as
`probe_null_behind_edge` for the whole 14-day window (forward window = probe window here) and never
govern it absent, which the legacy writer does. Days after the newest scene are
`newer_than_probed_edge` (no alarm); legacy asked them and refused them `source_unsettled`, so the
probe only saves those sends. An unreadable probe body is `invalid`.

`sortby=-properties.datetime` is the one request shape the legacy reader never sent; verify it
against the live API before G6 (open item).

## Wave O fetch: the whole day walk is one unit

A day is a STAC paging walk plus `Range` reads against per-scene COG hrefs on another host, sized
only once the catalogue answers, so `plan_requests` (synchronous, no I/O) cannot pre-plan it and the
`ProviderClient` (declared endpoint + query) cannot express it. `fetch` therefore calls
`ingest/vegetation.py::collect_ndvi_grid_records` through `ingest/http.py::upstream_client(COG_BOUNDS)`,
exactly as the legacy `source.py::fetch_vegetation_day` does: Wave O's "existing source module's
upstream_client" alternative, the same one `watersheds/nhdplus.py` documents. Consequences:

- One logical request per day on the unweighted ledger; the real page and COG sends are invisible
  to it, by design.
- `requested_parameters` is empty, so no unit is ever checkpointed: a checkpoint replay goes back
  through `fetch`, which would re-walk the network instead of replaying.
- `PER_UNIT_FETCH_CAP_SECONDS` (330 s, `runner/fetch.py`) wraps the whole walk; a heavy day past it is
  cancelled and restarted whole (up to 3 attempts). The legacy turn budgeted 900 s for ONE day, so
  this can bite; the fix is the CQ-9 per-lane unit-cap override, not this package.
- `SourceResponse.body` is the day's record digest text (the COG bytes are never held), and
  `Written.source_digest` is set explicitly because rows carry the capture instant.

## Support (`support.py`)

The published rows key on `agri.spatial_cell.id`, a random `gen_random_uuid()` that no lattice
arithmetic can derive, and a strategy has no database session. The legacy writer reads it with
`SELECT cell.id, cell.cell_key, ST_X(cell.centroid), ST_Y(cell.centroid) FROM agri.spatial_cell
WHERE grid_name = 'sentinel2-ndvi-0p25deg'` (`direct/vegetation/support.py::_SUPPORT_CELLS_SQL`). This
package reads the same four columns from a pinned snapshot, `support_cells.json` beside this file
(a JSON list of `{cell_id, cell_key, cell_longitude, cell_latitude}`), cached per process. It is NOT
committed yet: generating it is a production read that needs an owner go (open item). Until it
exists, the probe reports `unavailable` naming the missing file, and the lane writes nothing (it is
dark anyway). `plan_requests` refuses a support whose keys or centroids differ from the region's
`analysis-0p25` lattice (`foundation/region/pnw.json`, prefix `sentinel2-ndvi-0p25deg:`); that check
replaces the legacy step/extent guard. The fetch bbox is derived from the cells with the legacy
formula, so the STAC query is byte-identical.

## Row identity and volatile columns

No `direct:` id exists here: the grain is `(cell_id, observed_day)`. `observation_checksum` is
`record_sha256`, byte-identical to legacy `source.py::_record_sha256` through `_shared/digest.py`
(`sort_keys=True`, compact, `allow_nan=True`); `day_response_sha256` restates `_day_response_sha256`
(`sort_keys=False`, compact). Both are new rows for the `_shared/AGENTS.md` digest table and a new
parity pair for `tests/lanes/test_direct_feature_id_parity.py` (open items: files outside this slice).
`data_available_at` is stamped from the fetch's capture instant (`rows()` reads
`responses[0].retrieved_at`), so it is the stream's one `volatile_columns` entry.

## Governed-plane promotion (`governed_plane_promotion.py`)

The legacy verb reads the forward lane's `layer=vegetation/kind=observed` base-rung partition and
hands its `(cell_key, metric_value)` rows to `execution/vegetation_ndvi_plane.py::
register_governed_partition_plane`, which writes `agri.*` only (rows land in
`agri.forecast_observation` via `sql/execution/insert_partition_observations.sql`) and keeps a
promotion receipt in the object store. That target is Postgres, and a config transform's output is a
Parquet `[[streams]]` entry; the only Parquet stream involved, `vegetation`, is the forward lane's
(S18: one owner per slug). So the TOML declares the input lane, NO streams, and is
`enabled = false` (never armed in production: absent from `PLANTGEO_JOB_EXECUTOR_ACTIVE_LANES`);
`derive` refuses every day naming the target. Porting it for real needs either a Parquet governed
output stream or a runner output hook for Postgres targets, an owner decision under the
Postgres-to-community pivot.
