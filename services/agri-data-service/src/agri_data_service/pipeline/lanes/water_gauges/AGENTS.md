# `pipeline/lanes/water_gauges/` — the `water-gauges-daily` strategy

Track `config_driven_ingestion_20260926`, spec §7a, plan Phase 3 (`w3-water-gauges`). One lane
(`lanes/water-gauges-daily.toml`, strategy key `water_gauges.usgs_water_data`). The local G3 preparation
sets `enabled = true` and `gap_fill_enabled = true`; production activation still requires the owner's
go and reviewed deployment. G4 serving remains separate. The source probe (P4) every rule below rests on is
`docs/lanes/water-gauges.md` §8.1; the gates are §8.4.

| module | what |
|---|---|
| `usgs_water_data.py` | `STRATEGY`: per-tile units, the day view, the named day, the identity, the digest, the rows |
| `../../../ingest/usgs_water_data.py` | the source contract: tiles, the two request shapes, the parsers, `publisher_named_day` |
| `../../../warehouse/schemas/water_gauges_daily.py` | the stream schema and its coarse-rung derivation |

Imports: `ingest`, `warehouse`, `pipeline/parquet` (the checkpoint body cap) and
`pipeline/runner/contract.py` only (S14). Never `pipeline/validation` or `pipeline/direct`.

## Units: per tile, and a tile that stays down

The 8 tiles of the region's `default_camera_envelope` at 4 degrees (`ingest/usgs_water_data.py::tile_boxes`)
are the legacy NWIS layout, so coverage is unchanged and each tile is its own retry grain (D9). Per tile:
one daily-values unit per run of at most 31 consecutive asked days, and one monitoring-locations unit (names
and time zones) over every asked day.

**A tile is whole for a day when both its units answered** (`day_view`); nothing from a partial tile is read.
The runner hands a `write_and_recheck` day the units that answered (`pipeline/runner/AGENTS.md` "The turn",
step 4), so one tile's 503 costs that tile's gauges only, never the other seven (review H1). `settle`
returns `Written(expected_units=len(planned_tiles(context.planned_units)), present_units=whole tiles)`: a
short day is written, reported `unwritten` with the failed units named, rewritten when more tiles answer
(`more_units`), and never replaced by a shorter answer (`fewer_units`). A names unit that fails holds its
tile for every day of the turn, because its site names and zones cover them all.

The stable source-unit identities are tile bboxes, independent of request date chunks. They are
persisted in the runner receipt and its yearly completeness proof. A partial historical day stays
in gap-fill's source backlog across restarts, including before the rolling-revision horizon; a
complete zoom ladder alone never proves the re-pull finished. A later partial answer may replace
an earlier one only when it contains every previously answered tile. Six tiles followed by seven
different tiles does not meet that condition. G4 history validation must pass the strategy's
`source_unit_ids` into the runner reader's census and require no source debt as well as no missing
ladder days. See `pipeline/runner/AGENTS.md` "Census" for the publication proof and crash ordering.

**One page per unit, one bad feature alone.** `limit` is the collection's maximum, 50,000; the densest tile
has about 250 stream sites, so 31 days is about 7,750 features. A `next` link means the sizing assumption
broke: the page is refused (`UsgsWaterDataPayloadError`, the unit's `strategy_error`), never silently
truncated, and never paged, because a checkpoint replays exactly one request
(`pipeline/runner/checkpoints.py::_ReplayClient`). A single feature the contract cannot read (another unit
or statistic, a non-number, a valued row without approval, a null inside `qualifier`) is dropped ALONE
(`ingest/usgs_water_data.py::RejectedFeature`), logged per unit as `water_gauges_daily_features_rejected`,
and counted on its named day in `Written.dropped_rows["rejected_feature"]`, which the S5 report sums as
`rows_dropped_by_reason`. Refusing the page instead would blank a whole tile for 31 days on every turn over
one quirky row.

**Checkpoint size.** `pipeline/parquet/source_checkpoint.py::CHECKPOINT_MAX_BODY_BYTES` is 2 MiB. A dense
tile over 31 days is about 3.5 MB (P4: about 607 bytes a feature), so such an answer is marked
checkpoint-ineligible in `fetch` instead of failing the write. With partial days written, a checkpoint only
matters for a day that stays wholly unwritten.

## The named day

`publisher_named_day(time)` = `date.fromisoformat(time[:10])`: the served string's first ten characters,
the rule `pipeline/validation/water_gauges.py::publisher_named_day` states, restated in `ingest` because a
strategy may not import `pipeline/validation`. `tests/lanes/water_gauges/test_usgs_water_data.py` holds the
two equal. P4 saw only date-only `time` strings; an offset timestamp would still name its prefix day.
Never a UTC window (`fetch_source_day_from_nwis` builds one and must not be reused) and never
`datetime.fromisoformat(...)` then convert: that conversion once moved 6,279 of 16,743 rows.

## Identity, duplicates and conflicts

The identity is `monitoring_location_id:time:statistic_id` verbatim (the schema's grain). A gauge on a
shared tile edge is served by both tiles: identical copies collapse. Two different values under one identity
(a revision landing between the two sends, or a second series at one site) cannot both be written and
neither can be preferred honestly (the daily collection carries no "primary" flag; only
`time-series-metadata` does, and it has no geometry to query by tile), so that identity is dropped for the
day, logged as `water_gauges_daily_identity_conflict`, and counted in `dropped_rows["identity_conflict"]`
(review M1). The next fire re-asks.

## Rows

- The first fourteen columns are the legacy `water-gauges` stream's, with the same types and nullability,
  so the web reader decodes both streams into one row shape (`src/lib/server/services/parquet-trpc-readers/
  water-gauges.ts::readWaterGaugeStream`). The daily-values columns follow them.
- A served null (P4: `qualifier: ["EQUIP"]`) or the -999999 sentinel drops the row, as the legacy DV walk
  did: a row claiming a gauge reported "no value" is a fabricated observation of an absence. These are not
  counted as dropped rows: the source served no value.
- `observed_at` is the named day at the site's STANDARD-time midnight, from the monitoring-locations
  `time_zone_abbreviation`; a daylight abbreviation (`PDT`, `MDT`) names its zone's standard offset. A site
  missing from the names answer, or with an unknown zone, is stamped at `regional_offset`: the standard
  offset most of the day's whole-tile sites name (PNW: `-08:00`), and `+00:00` only when no site names a
  known zone (review M2; a UTC-midnight stamp reads as the previous local evening). Nothing reads a day
  back from it.
- `ingested_at` is the retrieval instant of the tile answer that carried the row.

## Rewrites (S11)

`day_source_digest` hashes every row fact except the retrieval instant: identity, value text, approval,
qualifiers, series id, point, name and zone. So Provisional -> Approved at the same value rewrites the day,
and a database refresh (new feature UUIDs, which P4 says are not stable) does not. Without an explicit
`Written.source_digest` the runner would digest the rows, whose `ingested_at` changes every fetch, and every
recheck would read as a revision.

**Late approvals** (review H2). USGS approves a water year months after it ends, long after the 14-day
forward window. `[days] revision_window_days = 550` makes every forward turn also re-ask one 31-day block of
published days behind the window (`pipeline/runner/windows.py::revision_block`), each block once every 19
turns, so a day written Provisional is rewritten Approved within 19 days of the approval, for 18
months. Forward turns cost 32 requests (16 forward, 16 revision). Days older than 550 days when first
written are gap-fill's and arrive mostly Approved already.

## Not here, on purpose

- **The manifest binding.** `usgs_water_data` (`ingest/usgs_water_data.py::USGS_WATER_DATA_SOURCE_SLUG`) is
  bound to the `water-gauges` layer at G4, replacing `usgs_nwis`. A second `water-gauges` binding now would
  break `src/__tests__/region/manifest-parity.test.ts` (it maps bindings by layer slug) and make
  `foundation/region/layer_availability.py::region_layer_availability` report whichever binding is last.
- **The API key.** P4: none needed. `ingest/provider_client.py` sends a key only to a `customer_host`; this
  provider has one host, so G2 needs a header-key change there first (`docs/lanes/water-gauges.md` §8.4).
