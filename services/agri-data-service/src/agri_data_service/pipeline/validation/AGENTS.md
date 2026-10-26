# Pipeline validation

Validation reads the governed source and the published object plane independently. A clean report
must not be inferred from the writer's own intermediate counts.

## Module verdicts (audit 2026-10-26)

No module here except `soil_survey.py` has a production caller. Each one was judged against the
config runner, which now owns most of this work for a lane that actually runs `executor = "config"`:
its census reports missing, incomplete and conflicting days (`pipeline/runner/census.py`), gap-fill
settles each owed day against the live source as either written or a governed absence, and G6
`--compare` rebuilds a day from upstream and diffs it against the bucket
(`pipeline/runner/turn.py::_Turn._compare`, `reader.canonical_digest`). **`water-gauges-daily` is the
only lane below on `executor = "config"`**; `drought-direct-forward`, `evacuation-zones-direct-forward`,
`fire-detections-direct-forward`, `sensors-direct-forward`, `weather-observations-direct-forward` and
`watersheds-direct-forward` stay `executor = "legacy"`. `--compare` is still allowed on them
(`turn.py` admits a legacy lane for `--compare` only): it fetches the live upstream through the
lane's config strategy and diffs that rebuild against the bucket the legacy adapter wrote. So a
live-source check exists ON DEMAND for every lane here -- but nothing schedules it. "No lane lost an
active check" means exactly that: none of the removed modules ever ran in production.

| Module | Verdict | Evidence |
|---|---|---|
| `soil_survey.py` | **wired**, keep | `pipeline/direct/soil_survey/__main__.py` `validate` verb |
| `vegetation.py` | **unwired; candidate ops check**, kept | `reconcile_against_source` is pure and tested. Its duplicate-source-release audit against `agri.forecast_observation` has no runner equivalent, because vegetation is not a config lane. Wiring it would be a new job and needs the owner's approval. |
| `watersheds.py` | **unwired; candidate ops check**, kept | `compare_against_source` is pure and tested. `watersheds-direct-forward` is `executor = "legacy"`; an on-demand `--compare` can diff a live rebuild against the bucket, but nothing schedules one, and this module's per-basin vintage diff is finer-grained. Wiring it would be a new job and needs the owner's approval. |
| `drought.py` | UNWIRED, REMOVED | No production caller since creation in `abf777ff`; no lane lost an active check. An on-demand `--compare` on `drought-direct-forward` (legacy) rebuilds from the live USDM feed; nothing schedules it. |
| `evacuation_zones.py` | UNWIRED, REMOVED | No production caller since creation in `abf777ff`; no lane lost an active check. `evacuation-zones-direct-forward` stays `executor = "legacy"`. |
| `sensors.py` | UNWIRED, REMOVED | No callers and no tests; no production caller since creation in `abf777ff`. An on-demand `--compare` on `sensors-direct-forward` (legacy) rebuilds from the live NWS feed; nothing schedules it. |
| `fire_detections.py` | UNWIRED, REMOVED | No callers and no tests; no production caller since creation in `abf777ff`; no lane lost an active check. |
| `signal.py` | superseded, deleted | It reconciled a Postgres-to-Parquet export (`pipeline/lanes/signal.py`) that was deleted in the 2026-09-12 greenfield cutover |
| `water_gauges.py` | superseded, deleted | Its only "caller" was a parity assertion in a test. `water-gauges-daily` is `executor = "config"`, so the NWIS reconciliation genuinely is replaced by `--compare` against the live source, and the named-day rule now lives in `ingest/usgs_water_data.py::publisher_named_day` |
| `weather_observations.py` | UNWIRED, REMOVED | No callers and no tests; no production caller since creation in `abf777ff`; no lane lost an active check. |

The former `vegetation_exact.py` and `weather_observations_exact.py` gates were deleted with the
2026-09-12 greenfield Parquet cutover (`fd602901`), so their sections are gone from this file too.
Other AGENTS files that still name them, or the deleted modules above, are stale on that point.

## Soil-survey candidate validation (SSURGO port, slice S2)

`soil_survey.py::validate_soil_survey_candidate` reconciles one prepared shard candidate
(`foundation/soil_survey/release.py::Candidate`) against a **current** USDA SDA census, the same
per-area checks `validate_soil_survey_release` runs on a day partition: delineation count on
`mupolygonkey`, and vintage staleness. It reads the rung-13 parts through any
`AvailabilityStorage`, so it runs **before** staging against the local capture root
(`pipeline/direct/soil_survey/local_storage.py::LocalCandidateStorage`, plan finding F8) and can
re-run against the bucket after staging. Either way every part is checksum-verified.

- **Bounded reads.** One call checks 1..`MAX_VALIDATION_AREAS` (50) areas and reads at most
  `MAX_VALIDATION_PART_BYTES` (128 MiB) of native parts. `candidate_validation_groups` /
  `group_areas_for_validation` split a shard's areas greedily, in sorted order, under both caps; an
  area whose own parts exceed 128 MiB is refused rather than read partially.
- **Q4 labels are checked, not trusted.** Owner Q4 serves every row, labelling unrepaired invalid
  geometry `invalid_unrepaired` and repaired geometry `repaired`. Each part's `geometry_quality`
  column must match the `repaired_rows` and `labelled_rows` its manifest `Part` records, and a null
  label is refused, so a manifest can never under-report what the bytes serve.
- **Network step.** Each area costs one SDA call (`HttpxSoilSurveySdaClient`) under a 120 s
  budget for the whole group, checked between areas rather than wrapped once around the whole
  loop: cancelling a whole-group `asyncio.timeout` mid-flight would abort the area in progress
  *and* discard every finding already collected. Instead each call gets its own remaining share
  via `asyncio.wait_for`, and once the deadline is passed every area not yet reached becomes its
  own `source_query_failed` finding instead of being silently dropped. One area's transport failure
  (or its own timeout) becomes a `source_query_failed` finding for that area only, never the end of
  the run.
