# Pipeline validation

Validation reads the governed source and the published object plane independently. A clean report
must not be inferred from the writer's own intermediate counts.

## Module verdicts (audit 2026-10-26)

No module here except `soil_survey.py` has a production caller. Each one was judged against the
config runner, which now owns most of this work: its census reports missing, incomplete and
conflicting days (`pipeline/runner/census.py`), gap-fill settles each owed day against the live
source as either written or a governed absence, and G6 `--compare` rebuilds a day from upstream and
diffs it against the bucket (`pipeline/runner/turn.py::_Turn._compare`, `reader.canonical_digest`).

| Module | Verdict | Evidence |
|---|---|---|
| `soil_survey.py` | **wired**, keep | `pipeline/direct/soil_survey/__main__.py` `validate` verb |
| `vegetation.py` | **unwired; candidate ops check**, kept | `reconcile_against_source` is pure and tested. Its duplicate-source-release audit against `agri.forecast_observation` has no runner equivalent, because vegetation is not a config lane. Wiring it would be a new job and needs the owner's approval. |
| `drought.py` | superseded, deleted | Runner census plus gap-fill on `drought-direct-forward` gives the same written / absent / conflict / incomplete / source-gap verdicts per release Tuesday |
| `evacuation_zones.py` | superseded, deleted | The source keeps current state only, so `--compare` on `evacuation-zones-direct-forward` (today vs. live feed) is the whole honest check |
| `sensors.py` | dead, deleted | No callers and no tests. Its existence-only check over NWS's 6-day window is what the forward census already does for `sensors-direct-forward` |
| `fire_detections.py` | superseded, deleted | No callers and no tests. It read a legacy `IngestionJobResult` cap signal; `pipeline/lanes/fire_detections` now settles capped days explicitly |
| `signal.py` | superseded, deleted | It reconciled a Postgres-to-Parquet export (`pipeline/lanes/signal.py`) that was deleted in the 2026-09-12 greenfield cutover |
| `water_gauges.py` | superseded, deleted | Its only "caller" was a parity assertion in a test. The NWIS reconciliation is replaced by `--compare` on `water-gauges-daily`, and the named-day rule now lives in `ingest/usgs_water_data.py::publisher_named_day` |
| `weather_observations.py` | superseded, deleted | No callers and no tests. It validated the legacy `geo.features` current-conditions export, not the config lane's output |
| `watersheds.py` | not assessed | Outside this audit's scope. It is test-only today |

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
