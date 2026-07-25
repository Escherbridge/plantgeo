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

`ForecastHindcastRun` and `ForecastHindcastValue` mirror the separate retrospective evaluation plane. A simulated cutoff is never an operational issue time. Finalized rows retain exact point, empirical interval, naive, actual-release, residual, and receipt evidence; the database sets their signal availability during finalization so future ML features can apply an honest as-of boundary.

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

`geospatial.py` is an append-only evidence foundation, not a recommendation plane. City, parcel, and property identities are immutable versions of stable keys; normalized features retain exact source-release/artifact lineage; and typed facts, derived features, and known gaps retain explicit support, resolution, inference-scale, nullable confidence, method-version, and relational input lineage. Model-derived inputs reference an immutable plane-specific validated run receipt with plan/code/output digests, never the generic job ledger. Every evidence row is explicitly not life-safety validated.
