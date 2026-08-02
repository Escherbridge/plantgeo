# Migration boundary

Alembic is the only component allowed to create or alter the `agri` schema. Runtime API and worker processes must never call `create_all`, `drop_all`, or extension DDL. The foundation revision is also forbidden from enabling extensions: it begins with an installed-extension preflight, so an operator must run the reviewed manual extension gate before Alembic creates `agri`.

The foundation revision is intentionally forward-only because downgrading would destroy source lineage, checkpoints, and published model evidence. Roll back by restoring a verified backup into a fresh database and deploying the prior application version. PostgreSQL extensions and the `agri` schema are shared infrastructure and are never removed by a downgrade. Once this revision has run outside disposable environments, do not change its preflight again: Alembic will not replay it for an already-versioned database, so later database-level changes need a new revision.

Keep future revisions explicit and deterministic. Do not call current ORM `metadata.create_all()` from a historical revision because replaying that revision would otherwise change as models evolve.

Release-set membership is mutable only in `draft`. The foundation triggers serialize draft finalization against membership writes, reject item inserts, updates, or deletes after validation, and freeze state, identity, and validation timestamps after the set first leaves draft. Future migrations must preserve that database-level invariant.

`20260720_0002` adds the typed historical-observation plane. It retains immutable source-release ownership, maps native source geometries to stable analysis cells, requires a spatial-cell identity for each normalized time-series point, records both cell- and source-level coverage audits, and exposes data only through a release-set-pinned contract function.

`20260722_0005` adds the SQL-first forecasting plane without loading forecast data. It bridges registered series to the existing pinned signal contract or append-only generic observations, carries bitemporal entity state and native/output support, and gates SQL or local-ML output behind checksummed feature snapshots, backtests, validated job outputs, immutable receipts, and the existing publication pointer. The regression functions use elapsed UTC time and empirical holdout residuals; they do not use the Synapse sample's row-number extrapolation or assumed Gaussian bands. Materialized serving aggregates are created `WITH NO DATA` and require an explicit reviewed refresh.

`20260722_0006` keeps retrospective evaluations out of the operational issue-time plane. SQL-linear hindcasts record an explicit simulated cutoff and a real server-set availability time; finalization recomputes regression points, leakage-safe calibration bands, naive values, actual values, and exact release checksums before freezing each receipt. The outcome view and signal function expose forecast-versus-actual residual series only after that recording time. Hindcasts are evaluation evidence and never join the operational publication view.

`20260722_0007` enforces that the entire empirical uncertainty-calibration horizon ends no later than the simulated forecast cutoff. A merely earlier calibration cutoff is insufficient because its evaluated future steps could otherwise overlap the hindcast target window.

`20260722_0008` preserves every finalized pre-existing hindcast under the exact `hindcast_v1` digest while requiring new runs to enter as staged `hindcast_v2`. V2 binds declared run/series/release/model/policy identities, the canonical policy definition, temporal/training declarations, and actual availability. A staged finalization also requires an active parent policy and at least its configured `min_backtest_points` residual-calibration samples. Once a policy is referenced by a finalized hindcast it is immutable; revised thresholds require a new policy identity. Never replace the v1 formula or backfill old receipts with a v2 checksum.

`20260723_0009` adds an immutable, geometry-valid WGS84 foundation for normalized source features, versioned city/parcel/property subjects, plane-specific analysis receipts, and typed intervention evidence inputs. Evidence lineage is relational and may reference multiple pinned source releases/features or checksummed historical rows; deferred and membership triggers reject unlined or unvalidated inputs. This plane does not create recommendations or claim life-safety validation.

Revision `0009` also verifies inline artifact bytes against their SHA-256
digest. First-reference checks lock release, artifact, and release-set parents;
after reference, lineage-critical content and membership are frozen while
controlled valid-to-retracted source lifecycle changes remain available.
Release sets retain the foundation revision's stricter post-validation freeze.
Pre-existing inline artifacts must pass a digest audit before the revision is
applied.

`20260723_0010` adds an evaluation-only forecast iteration pipeline without
changing the operational run, receipt, hindcast digest, or publication planes.
The canonical view describes registered series and provider/support metadata;
each iteration snapshots that JSON contract and the exact training-release
licenses. A compact server-written high-water ledger records contract,
release-set, and source-release content changes at release granularity, avoiding
one ledger row per historical observation while conservatively preventing
backdated replay. Daily alignment rejects governed/declared support conflicts,
source support coarser than one day, and implicit subdaily aggregation.

The bootstrap samples historical consecutive daily increments with
SHA-256-derived deterministic indices and persists checksummed p10/p50/p90
values for a default 30-day horizon. Checksum functions pin PostgreSQL date,
interval, float, and UTC rendering settings. Iterations are terminally
immutable, cannot join `forecast_publication_item`, and receive actuals later
through a separately checksummed source-lineage table. Actual digest v2 waits
for a complete UTC bucket and binds the later release-set identity, manifest,
observation checksums, and persisted license snapshots under the approved
source URL/citation gate.

These outputs are evaluation signals for later models, not validated
life-safety predictions. Every iteration is labeled `evaluation_only` and
distinguishes an as-of-pinned release from a retrospective pinned release.
LOCF may bridge only the immediately preceding day. A realization is unique
per iteration value; corrected governed inputs require a new release set and
iteration. P10, p50, and p90 are empirical bootstrap path quantiles, not
calibrated confidence or life-safety bounds; v1 assumes exchangeable daily
increments and does not model seasonality or autocorrelation.

`20260725_0012` keeps the constrained source loader from needing read grants on
the intervention plane merely to finalize a release set. The existing
intervention-parent freeze logic now runs under the dedicated
`plantgeo_intervention_guard_owner` NOLOGIN role with only its required reads,
an `id`-only release-set lock capability, and a fixed `pg_catalog, agri` search
path. The role has no memberships or schema-create capability after ownership
transfer; direct execution and public schema creation remain revoked.

`20260725_0013` adds the first strategy-selection contract. It is forward-only
because treatment/control labels, candidate scores, abstentions, and selection
receipts are audit evidence. Its two additions to pre-existing forecasting
tables are nullable so historical metric-forecast rows remain valid;
strategy-selection training alone requires a finalized label release and a
checksummed feature artifact output. The model artifact output metadata and
training receipt must repeat the finalized label-release checksum; output
metadata must also repeat PostgreSQL's checksum of the exact
`strategy_labels_v1` export text. Selection finalization and its digest verify
that binding. Reviewed outcome and policy
checksums are computed by PostgreSQL, while insert triggers require
draft/staging parents before append-only label episodes or candidates are
accepted. The validated label export reproduces the strict
`strategy_labels_v1` feature order, assignment-time covariates, cohorts, raw
values, availability timestamps, and release checksum.
This revision refuses effect-tier finalization; a later additive revision must
persist cluster-bootstrap, placebo, negative-control, and positive
best-vs-second lower-bound evidence before enabling it.

`20260801_0014` makes hindcast finalization reproducible and its quality gates
able to fail. `forecast_hindcast_run.actual_knowledge_as_of` stores the
actuals/knowledge horizon once, at first finalization; every later read —
regression, residual bands, actual lineage, naive baseline, horizon
completeness — is pinned to it, so re-verifying a finalized receipt cannot drift
with the wall clock. Pre-existing finalized rows are backfilled from
`finalized_at` (the server clock of the transaction that performed those reads)
or, for `as_recorded` runs, from `simulated_cutoff_time`; that is a
reconstruction at the resolution of the recorded finalize time, not a byte-exact
replay of the original `clock_timestamp()`.

New `hindcast_v3` runs redefine `coverage_fraction` as horizon completeness —
ideal horizon steps with an actual at the pinned knowledge horizon, over
`horizon_steps` — and must record exactly those steps, so `expected_value_count`
may be smaller than `horizon_steps` for v3 and stays strictly equal for v1/v2.
`forecast_quality_policy.min_interval_coverage_fraction` (0.8, the nominal
coverage of a p10–p90 band) is wired into both `computed_pass` and the
finalization trigger. `hindcast_v1` and `hindcast_v2` keep their exact
preimages, their old coverage formula, and no interval gate; only the v3
preimage carries `plantgeo-forecast-quality-policy-v2`.
`actual_knowledge_as_of` is deliberately outside every preimage, like
`finalized_at`: the caller must be able to compute the expected checksum before
the server sets it.

The same revision corrects the inverted strategy-selection as-of gate
(`iteration.cutoff_time <= data_cutoff`) into one canonical predicate,
`agri.strategy_selection_cutoff_violation`, called by both the finalizer and the
revision's audit pass; finalized receipts violating the corrected rule are
flagged `audit_state = 'cutoff_violation'` with a reason and flag time rather
than deleted or grandfathered, and the flag is a one-way post-hoc annotation
outside the receipt preimage so it cannot invalidate the checksum that records
what was claimed. `agri.strategy_selection_quality_evidence` adds a hard gate:
selection finalization requires a finalized `quality_passed` hindcast for the
backing series (and model, for publishable receipts), available by the receipt's
issue time. Finally, the four finalizers reject a NULL expected checksum
explicitly (`NULL !~ pattern` is NULL, not true, so it used to skip the format
gate), and the receipt-digest dispatch raises on any version outside the known
set.

**Operator note.** Before `20260801_0014`, `coverage_fraction` was always exactly
`1.0` (see the defect list above), so any pre-existing `forecast_quality_policy`
row's `min_coverage_fraction` was never actually exercised as a gate -- it could
not fail regardless of its stored value. Existing policy rows are not
renumbered or backfilled by this revision. Before relying on a v3 hindcast run
against an operational policy row, an operator must review that row's
`min_coverage_fraction` (and the new `min_interval_coverage_fraction`, backfilled
to `0.8`) and confirm the threshold is the one actually intended now that both
gates can fail.
