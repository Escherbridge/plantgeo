# Strategy-selection label audit — 2026-07-25

## Result

PlantGeo cannot produce the first real strategy-selection benchmark from the
authorized local evidence currently available. No inspected source binds an
independent subject to a governed treatment strategy or an eligible untreated
control, assignment/cohort time, intervention window, spatial block, matured
baseline and outcome values, assignment-time covariates, and source-release
lineage.

The user assertion in the originating task was only that
“intervention/control outcome labels exist.” It did not identify a file,
database relation, source system, outcome, or mapping. Boise forecast actuals
were explicitly excluded from this audit: they are forecast-error labels, not
intervention-effect outcomes.

No label release was staged or finalized, no `strategy_labels_v1` bundle was
exported, no estimator was fit, and no training or selection evidence was
registered. This is a source-missing abstention, not a causal or feasibility
result.

## Authorized sources inspected

### PostgreSQL

The isolated warehouse on `127.0.0.1:5442` contained:

- `plantgeo`, at Alembic `20260722_0007`;
- `plantgeo_boise_completion_20260725`, at `20260725_0011`, with no
  `analysis_subject`, `strategies`, `intervention_analysis_run`,
  `intervention_evidence_input`, or `intervention_evidence_lineage` rows;
- `plantgeo_geospatial_test_20260723_1421`, at `20260723_0010`, with two
  context subjects, 17 context/gap evidence rows, one validated context run,
  and no strategy rows.

The 17 geospatial rows describe Boise/Hillside-to-Hollow boundaries, area,
property containment, WUI classification/overlap/vintage, and nine explicit
known gaps. They contain no treatment assignment, eligible control, or
pre/post outcome.

Native PostgreSQL 16 was found on port `5433` rather than the documented
`5432`. Its `plantgeo` database was empty, while `pgt` contained only unrelated
time-bank tables. The stopped legacy PlantGeo PostGIS volume was recovered
read-only through a temporary non-conflicting container; it contained only
`geo.features`, `geo.layers`, and `tracking.positions`.

Five local custom-format PlantGeo backups from July 21–23 were inventoried with
`pg_restore --list`. None contained the strategy-label contract or populated
intervention outcome tables, and the latest archived `agri.strategies` data was
empty.

### Files and local evidence

Searches covered both the delegated worktree and the main checkout, including
hidden `.omc`/`.mpg` state, Conductor tracks, plans, reports, ignored local-run
stores, generated test artifacts, and Parquet/JSON/JSONL evidence.

The principal local-run inventories were:

- four Boise intervention capture roots containing boundaries,
  classifications, and provenance receipts only;
- 5,968 NASA historical JSON files and 1,463 Parquet files with observation
  columns only;
- four historical USDM JSON files and related logs;
- Boise parcel, WUI, Census, ArcGIS, Natural Earth, and forecast-iteration
  evidence;
- synthetic strategy-label rows under tests only.

The validated Hillside-to-Hollow capture is context evidence, not a label
source. Its custody identifiers are:

- plan checksum:
  `39d01682649d68a9bfb1d7ee4ebd258a3cfb627b6dc192df8e84be6c1eb2ac73`;
- receipt-set checksum:
  `c17baeefc8c758b577989dcc02f3e8b78eb8354199f029580a7781de9906874a`;
- Census Boise boundary:
  `0025169cd38411e915edbb3f03e89f41a6ba7c471f19f807a55e6850107da96c`;
- OSM reserve:
  `7552170d903f8f6313c79d20c186f1c192cd4db5bc3865c99919f8458fa789b3`;
- versioned OSM reference:
  `b8f3e0e9a7312bdbcf796156a4257eebe1daec8f6880e7c5ead3de0552ca13e0`;
- USFS WUI:
  `b3a198baeb277b65735381240e9919be317978b6bb6a8ff75ac88ce24ae212da`.

These checksums must not be represented as strategy label, bundle, model, or
selection checksums.

## Prepared development boundary

`plantgeo_strategy_benchmark_20260726` was cloned locally from the retained
Boise evidence database and migrated through Alembic `20260725_0013`. The new
strategy outcome, label, policy, candidate, and receipt relations are present
and empty. The label export and both finalization functions remain unavailable
to `PUBLIC`.

The database is ready for a real source import, but its empty strategy plane is
intentional. Creating synthetic episodes there would defeat the purpose of
this benchmark.

## Benchmark status

| Benchmark element | Status |
| --- | --- |
| Matched difference-in-differences | Not fit — no real label bundle |
| Cross-fitted AIPW | Not fit — no real label bundle |
| Doubly robust ridge learner | Not fit — no real label bundle |
| T-learner sensitivity | Not fit — no real label bundle |
| Expanding-time / held-out-block folds | Not constructed |
| Treatment/control support | Not computable |
| Propensity overlap and stabilized weights | Not computable |
| Effective sample size | Not computable |
| Weighted covariate balance | Not computable |
| Estimator agreement | Not computable |
| Best-versus-second paired cluster contrast | Not computable |
| Research ranking | Abstained before fit: source labels missing |
| Label-release checksum | None |
| Canonical bundle checksum | None |
| Artifact checksum | None |
| Training receipt | Not registered |
| Selection receipt | Not registered |

No forecast was published, no forecast materialization or reconciliation was
triggered, and `effect_candidate` remains disabled.

## Minimum concrete source mapping still required

A source owner must identify the following without retrospective invention:

1. Stable independent subject keys/geometries and the documented eligibility
   or risk set.
2. Governed treatment strategy identity/version and genuinely eligible
   untreated controls. Controls use `strategy_id = NULL`; there is no synthetic
   control strategy.
3. Cohort and assignment time, assignment mechanism/probability, intervention
   start/end, and exposure or intensity.
4. A predeclared spatial-block scheme and block key for every subject.
5. An approved outcome definition: metric, unit, benefit direction, smallest
   meaningful effect, aggregation/transform, exact baseline/outcome durations,
   and eligibility policy.
6. Same-metric numeric observed facts covering each subject’s baseline and
   fully matured outcome windows.
7. An ordered covariate schema, values, and proof that every predictive input
   was available no later than assignment.
8. Source release/release-set lineage, observation and availability times,
   artifact or normalized-record locators, record checksums, and license/custody
   evidence.
9. At least the current support floor per candidate comparison: 100 treated
   units, 200 eligible controls, eight spatial blocks, four cohorts, and 20
   nonempty block/cohort clusters.

The database-free `strategy-label-map-preflight` command and its incomplete
example manifest provide the next handoff boundary. They validate that a named
external source maps every required concept and emit a canonical mapping
checksum only when the mapping is complete. They do not create episodes or
write to PostgreSQL.

## Verification

- A fresh empty database replayed every Alembic revision from `20260719_0001`
  through `20260725_0013`.
- Ruff passed for `src` and `tests`.
- MyPy passed for 50 source files.
- Pytest completed with 240 passed and 10 environment-gated skips; the live
  constrained-loader finalization proof ran and passed.
- The PostgreSQL 16 live schema-parity gate was then enabled explicitly and
  passed against the fresh migration replay.
- Independent review approved after closing one P1: training validation now
  compares the artifact metadata’s exact exported-bundle checksum with
  PostgreSQL’s authoritative
  `strategy_label_bundle_checksum(label_release.id)`. Altered bundle rows can
  no longer retain only the release checksum and register as the validated
  export.
