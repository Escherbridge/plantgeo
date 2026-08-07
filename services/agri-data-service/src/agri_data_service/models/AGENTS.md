# Agri model boundary

All tables in this directory are owned by Alembic and live in the `agri` schema. Foreign keys stay schema-qualified so connection `search_path` cannot redirect writes into application tables.

The durable ledger uses PostgreSQL as the source of truth for received artifacts and publications. Phase-one ETL, forecast, and model compute use the local manifest contract in `execution/` and do not dispatch through Celery. Work is at-least-once: `logical_run_key` and shard keys prevent duplicate intent; local checkpoints are append-only and checksummed; the database lease/fencing fields remain the recovery contract for bounded publication-side work; immutable outputs are exposed only by advancing `publication_pointer` in a transaction.

`release_set.manifest_checksum` is part of local run identity and is rechecked at stage and commit. After a set leaves `draft`, database triggers freeze its state, identity, validation timestamps, and membership. Withdrawal is a new publication-pointer rollback or tombstone, never mutation of pinned lineage.

`historical_promotion_bundle` is a private transfer ledger, not a serving-data table. Its immutable manifest is staged against a draft release set; bounded typed chunks have idempotent checksummed receipts, and source artifact descriptors are proved by separate raw-byte receipts. Finalization may validate the target release set only after every expected chunk, artifact, source-release, geometry, and coverage invariant has been rechecked.

`cell_source_crosswalk.spatial_support_kind`, native resolution, mapping method, and coverage are immutable source-support evidence. The pinned historical function exposes them beside each observation and marks acre-scale compatibility only for a single fully covered native grid cell at 64 metres or finer. It must never infer field precision from the requested AOI size.

Bounded local artifacts may use `artifact.storage_class=database_inline`; their bytes, declared size, and SHA-256 digest must agree. A local publication pointer remains `artifact_only` until a typed loader validates and promotes its rows into a serving table. Never query generic artifact bytes from a browser-facing route.

`job_event.detail` is for redacted structured diagnostics, not source payloads, credentials, private locations, or model training rows. A partition manager must create dated partitions and remove event partitions after 30 days; the default partition only prevents event loss before that manager exists.

Strategy and companion records default to `draft`. Strategy approval requires a reviewer, timestamp, citation, source URL, jurisdiction, and explicit limitations. The first strategy seeds contain only four USDA NRCS practice identities and definitions; all unsupported climate, soil, slope, labor, timing, and impact values are `NULL`. National standards remain definition/discovery sources until a reviewer verifies the exact version, applicability, and current local guidance.

`forecasting.py` mirrors the additive SQL forecasting plane. `ForecastSeries` distinguishes exact source variants and raw/native, resampled, or aggregate support; `ForecastEntityState` and observations are append-only. Feature, training, backtest, forecast, and publication rows retain release/job/output checksums so a local ML result cannot serve without a validated local training receipt. `model_purpose='strategy_selection'` records the intended future use, but no strategy candidate model output is represented as an effect claim: that contract remains gated on the strategy-selection requirements interview and reviewed intervention/outcome evidence.

## Ensemble quantile carriage, and why an upstream ensemble cannot yet be a receipt

`ForecastReceipt.quantile_levels` and `ForecastValue.quantile_values` carry an arbitrary quantile
set as-is: the levels are a `float8[]` guarded by `agri.forecast_quantiles_valid` plus a
`0.5 = ANY(...)` CHECK, and the values are a JSONB object keyed by the level rendered as a string.
Nothing in the database ties those two together. `quantile_values` has a `'{}'` server default and
no CHECK, so a writer bug produces a finalized receipt whose declared levels and stored keys
disagree and no constraint catches it. That agreement is therefore a writer invariant, enforced in
`execution/ensemble_forecast.py::require_consistent_quantile_carriage` together with the median
mirror (`point_value` must equal the `0.5` entry) and the p10/p50/p90 mirrors. One canonical level
renderer, `format_quantile_level_key`, is the only thing allowed to build those keys; two renderers
would silently split one level into two objects. `ordered_bands` is pairwise null-tolerant in SQL,
so an unset mirror never fails there — the lane refuses an unset mirror for a level it declared.

The Open-Meteo Ensemble lane stages receipts but does not write them, and that is a schema blocker,
not an omission. Every `ForecastReceipt` requires a `forecast_run_id`, and `ForecastRun`'s
`ck_forecast_run_method` closes `forecast_method` to `('sql_linear', 'ml')` — mirrored in the model
above and in `routes/forecasts.py`, which returns the value to callers. An upstream numerical
ensemble reduced to empirical quantiles is neither: declaring `sql_linear` asserts the SQL baseline
produced the numbers, and `ml` additionally requires a validated local `ForecastTrainingRun` and a
model artifact that do not exist. Both are false provenance on a serving surface. Until a migration
widens that CHECK (in the DB, in `ForecastRun`, in `ForecastModel.ck_forecast_model_kind`, and in
the route's `Literal`), the lane writes a checksummed staged document holding the exact receipt and
value payloads and reports `warehouse_persistence = blocked_forecast_method_check`. The quantile
columns themselves need no migration; only the receipt's owner does.

Revision `20260803_0018` retired the hindcast plane (`ForecastHindcastRun`, `ForecastHindcastValue`) and the status-to-checksum evidence CHECKs on `forecast_run`, `forecast_training_run`, `forecast_publication`, and `forecast_iteration`. Only `ck_forecast_receipt_finalized_evidence` survives, because `forecast_receipt.receipt_checksum` gates the ML serving view and has no SQL function behind it. Every plain checksum column and its format CHECK is retained, so the reproducibility record is intact; what is gone is the database-side enforcement that a validated or published row must already carry its digest. Those invariants now belong to the CLI, and a reader of `Base.metadata` alone will over-read what the schema guarantees.

`ForecastIteration` and its value/actual models mirror the generic daily
bootstrap evaluation plane. They intentionally have no relationship to
`ForecastPublication`: a stored iteration may be inspected and reconciled with
later observations, but it cannot serve as an operational forecast. Low,
median, high, actual, residual, error, and interval-coverage values become
time-honest model signals only after their database-recorded availability.
`ForecastInputRecordedAt` is the compact server-written high-water boundary for
contract, release-set, and source-release content changes; it is not a row per
observation. Each iteration retains its canonical contract and training license
snapshots. A single actual is retained per iteration value and digest v2 binds
the separate validated release set plus exact observation and release-license
inputs. Corrected governed inputs produce a new release set and iteration so
the previous evaluation remains auditable.

`ForecastIterationValue.value_checksum` is a plain nullable column, not a
generated one: `20260803_0018` dropped the expression so `pg_restore` no longer
has to recompute it and `extra_float_digits` can no longer leak into the digest.
`agri.materialize_forecast_iteration` is now the only writer, and it computes
the checksum explicitly in its INSERT. A row inserted by anything else leaves it
NULL, and `agri.forecast_iteration_receipt_checksum` will silently omit that row
from the receipt digest rather than fail, so never insert into
`forecast_iteration_value` outside the procedure. `ForecastIteration.purpose`
likewise lost its CHECK and now defaults to `serving`; keeping an evaluation
iteration out of a serving surface is a CLI invariant, not a schema one.

`geospatial.py` is an append-only evidence foundation, not a recommendation plane. City, parcel, and property identities are immutable versions of stable keys; normalized features retain exact source-release/artifact lineage; and typed facts, derived features, and known gaps retain explicit support, resolution, inference-scale, nullable confidence, method-version, and relational input lineage. Model-derived inputs reference an immutable plane-specific validated run receipt with plan/code/output digests, never the generic job ledger. Every evidence row is explicitly not life-safety validated.

`strategy_selection.py` separates governed treatment/control labels from model
outputs. A label episode binds one subject, explicit treatment or control arm,
cohort and assignment time, ordered assignment-time covariates, raw
baseline/outcome evidence, availability times, and spatial block inside a
checksum-finalized label release. The release pins the exact feature-name
order, and the outcome definition pins the smallest meaningful effect.
Selection receipts reuse forecast model, feature-snapshot, and local-training
lineage, then bind either an evaluation-only iteration or a published forecast
receipt. The exported bundle carries the finalized label-release checksum;
the model artifact, job-output metadata, training row, and selection digest
must repeat it so a model trained on one release cannot be rebound to another.

Definitions and policies still enter as drafts (`require_strategy_initial_state`)
and still receive server-computed checksums only on their immutable review
transition: `20260803_0018` keeps `guard_strategy_review_change` precisely
because it is the sole caller of `strategy_outcome_definition_checksum` and
`strategy_selection_policy_checksum`. Episodes and candidates remain immutable
once written (`guard_forecast_immutable_rows`).

What `20260803_0018` removed from this plane is enforcement, not structure. The
tables, columns and every checksum format CHECK survive; gone are the
status-to-checksum evidence CHECKs on `strategy_outcome_definition`,
`strategy_label_release`, `strategy_selection_policy` and
`strategy_selection_receipt`, the parent-staging insert guards on episodes and
candidates, the release/receipt change guards, both `finalize_strategy_*`
functions, and `strategy_selection_quality_evidence` (it INNER JOINed the
retired hindcast view). So the database no longer asserts that a `validated`
release or a `finalized` receipt already carries its digest, and no longer
blocks a child insert under a non-staging parent. Those are CLI invariants now,
and a reader of `Base.metadata` alone will over-read what the schema guarantees.

`ck_strategy_selection_receipt_claim_tier` is retained and still confines
`effect_candidate` to `execution_mode = 'publishable'`, but the blanket refusal
to finalize an `effect_candidate` lived in the dropped finalizer and is no
longer enforced anywhere in the database. Before that state is opened, a future
revision must add cluster-bootstrap, placebo/negative-control, and positive
best-vs-second lower-bound evidence.
