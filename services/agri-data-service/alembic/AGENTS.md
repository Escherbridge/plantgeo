# Migration boundary

Alembic is the only component allowed to create or alter the `agri` schema. Runtime API and worker processes must never call `create_all`, `drop_all`, or extension DDL. The foundation revision is also forbidden from enabling extensions: it begins with an installed-extension preflight, so an operator must run the reviewed manual extension gate before Alembic creates `agri`.

The foundation revision is intentionally forward-only because downgrading would destroy source lineage, checkpoints, and published model evidence. Roll back by restoring a verified backup into a fresh database and deploying the prior application version. PostgreSQL extensions and the `agri` schema are shared infrastructure and are never removed by a downgrade. Once this revision has run outside disposable environments, do not change its preflight again: Alembic will not replay it for an already-versioned database, so later database-level changes need a new revision.

Keep future revisions explicit and deterministic. Do not call current ORM `metadata.create_all()` from a historical revision because replaying that revision would otherwise change as models evolve.

**Since the 2026-08-25 collapse, every new revision must also be idempotent against the declarative tree.** The head `20260825_0000` builds the schema by executing `db/agri/**`, and `db/tools/regenerate.py` rebuilds that tree from the migration head — so once you regenerate, the tree contains your revision's own objects and the *next* build from empty applies them twice. New tables, columns, indexes and constraints therefore need `IF NOT EXISTS` (or a `pg_constraint` `NOT EXISTS` probe, since PostgreSQL has no `ADD CONSTRAINT IF NOT EXISTS`); programmable objects are already safe via `or_replace=True` or drop-then-create. The rule, the table of shapes, and the two tests that enforce it are in `../db/AGENTS.md` § *Layering a revision on the greenfield baseline*. `alembic/archive/AGENTS.md` covers the archive itself.

**Stamping an existing database to the baseline is gated by `db/tools/verify_stamp_target.py`.** It is read-only on its target and checks four things before anyone runs `alembic stamp`: the connected database is the one named on the command line, `timescaledb` is already absent (stamping skips `20260825_0026`, whose only job was dropping it, and nothing afterwards can ever notice), the target sits at `20260817_0025` or `20260825_0026`, and its `agri` schema is byte-identical to a freshly baseline-built one. Privilege differences are reported, not gated — `alembic stamp` executes no SQL, so it fixes none of them.

Release-set membership was mutable only in `draft`, enforced by foundation triggers that serialized draft finalization against membership writes, rejected item writes after validation, and froze state, identity and validation timestamps once the set left draft. `20260803_0018` retires those triggers (`release_set_identity_freeze`, `release_set_membership_draft_only`, `enforce_release_set_freeze`, `enforce_release_set_membership_draft`) along with the rest of the enforcement layer. The rule still holds as a contract; it is now the CLI's to enforce, and no future migration is required to reinstate it in the database.

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

`20260802_0016` adds the evaluation-only assignment-time covariate/feature
layer: `agri.covariate_feature_schema` pins an ordered, contiguous 40-name
covariate vector (`agri_covariates_v1`) the way `0013`'s label release pins its
trainer feature order; `agri.covariate_declared_gap` names ERA5-Land as an
explicit credential-gated gap so an absent stream is never rendered as empty
covariate columns; `agri.covariate_daily_features` emits one row per
(cell, UTC day, feature) over the house day spine; and
`agri.covariate_vector_manifest` returns a single checksummed provenance and
completion summary for a window.

Every covariate is **strictly lagged** (`lag_days >= 1` for every
meteorology/drought feature; the two calendar features are deterministic
functions of the row's own date). A feature row for day D therefore cannot
contain any day-D observation, so the same layer is safe as the assignment-time
covariate vector for a day-D decision and as trainer input for a day-D target.
The availability gate is `signal_observation.data_available_at <=
p_as_of_time` (and, inside `drought_class_daily_series`, the polygon's own
`data_available_at`) -- never a simulated cutoff. Partial stays partial: a
rolling window missing any input returns `feature_value = NULL` with
`input_count < expected_input_count` rather than a mean over the survivors, and
an unresolved drought class stays `NULL` rather than becoming class 0.

Because `uq_signal_observation_release_cell_signal_time` includes
`source_release_id`, a re-ingest or a revision legitimately stores several
admissible rows for one `(cell, signal, observed_at, support_key)`. The
observation CTE therefore takes `DISTINCT ON (signal_name, UTC day)` ordered by
`data_available_at DESC` with a deterministic tie-break, so completeness counts
distinct **days** and a duplicated day can neither double-count into a rolling
mean nor make a short window look complete. `covariate_vector_manifest`'s
lineage scan mirrors the feature reader's `support_key`/`quality_flag` filters,
so a release that contributed nothing cannot appear in `source_release_ids` or
perturb `manifest_checksum`.

The revision also rewrites `agri.drought_class_daily_series`' body (body-only,
`or_replace`) to hoist the per-day `ST_Intersects` into one materialized
admissible-polygon CTE. A four-year Boise window went from ~11 minutes to
~0.6 s, which is what makes the covariate layer usable as trainer input at all.
For an **existing** cell the rewrite is row-identical -- the day spine, the
issue-date/severity/`geometry_checksum` tie-break order, and both availability
gates are preserved, and a two-way `EXCEPT ALL` differential over checksum ties,
non-intersecting polygons, window-edge issue dates, >7-day imputation and both
gate boundaries found no mismatch.

It is **not** identical by construction, and the first draft of it was wrong:
moving the `cell` CTE into the `admissible` subquery dropped the outer
`CROSS JOIN cell`, so an unknown `p_cell_id` stopped returning zero rows and
started returning one all-NULL `is_imputed = true` row per spine day, echoing
the caller's own cell_id back as though it were a real cell whose drought was
merely unresolvable -- and that propagated into
`covariate_daily_features` as `drought_severity_imputed_lag_1 = 1.0` across the
whole window. The outer `CROSS JOIN cell` is restored, and the 0011 contract
test now covers the three paths the hoist actually touches (checksum tie-break,
a non-intersecting polygon, an unknown cell), so the claim is guarded rather
than asserted. `covariate_daily_features` cross-joins the same cell existence
check onto its own day spine for the same reason.

All four functions are evaluation-only reads: none joins
`v_forecast_series_serving`, `forecast_publication`, `forecast_publication_item`
or any receipt surface, and none is granted to
`plantgeo_forecast_reader`/`_writer`/`_publisher`/`_mv_refresher` --
`REVOKE EXECUTE ... FROM PUBLIC` only, with `plantgeo_local_developer`
inheriting `EXECUTE` from standing default privileges.

**Operator note.** Before `20260801_0014`, `coverage_fraction` was always exactly
`1.0` (see the defect list above), so any pre-existing `forecast_quality_policy`
row's `min_coverage_fraction` was never actually exercised as a gate -- it could
not fail regardless of its stored value. Existing policy rows are not
renumbered or backfilled by this revision. Before relying on a v3 hindcast run
against an operational policy row, an operator must review that row's
`min_coverage_fraction` (and the new `min_interval_coverage_fraction`, backfilled
to `0.8`) and confirm the threshold is the one actually intended now that both
gates can fail.

`20260803_0017` makes the warehouse restorable from its own dump and finishes a
half-applied checksum pin. Both defects were found by the populated-restore
rehearsal, both reproduce on PostgreSQL 16, and neither is visible to a
DDL-only rehearsal against an empty database.

`pg_restore` runs with `search_path = ''`. `forecast_iteration_value.value_checksum`
is a **stored generated** column, so its values are omitted from the dump and
recomputed by the restoring server during `COPY`; that calls
`agri.forecast_iteration_value_checksum`, which called `digest()` unqualified
against a `pgcrypto` that lives in `public`. A stock unattended `pg_restore`
therefore aborted the data load with
`function digest(text, unknown) does not exist`. One routine blocked a restore;
**21** shared the latent pattern (a `public`-schema extension function called
unqualified with no `search_path` pin) and all 21 are pinned to
`public, pg_catalog`. The count was re-derived from the live catalogue, and
disagrees with the rehearsal report's "20 to fix": the report counted
`enforce_intervention_lineage_release_membership` in the set and then subtracted
it again as already-pinned, but that routine qualifies its call as
`public.digest(...)` and never belonged there. None of the 21 already carried a
`search_path`, and none is `SECURITY DEFINER` -- the twelve that are keep their
existing `pg_catalog, agri` pin untouched.

The revision uses `ALTER ROUTINE ... SET search_path`, never `CREATE OR REPLACE`:
the ALTER *adds* to `proconfig` and preserves each routine's determinism
settings, while a replace that omitted them would silently drop the
`TimeZone`/`DateStyle`/`IntervalStyle`/`extra_float_digits` pins the checksums
depend on. No function body changes, so every `md5(prosrc)` is unchanged.

The second defect is a live receipt-integrity one.
`agri.forecast_hindcast_value_checksum` rendered six `double precision`
arguments through `::text` while pinning only `TimeZone`, so session
`extra_float_digits` leaked into a governed checksum -- measured, identical
inputs produced two different digests. It now pins the same four GUCs as the
correct control, `forecast_iteration_value_checksum`. Harmless only because
`forecast_hindcast_value` is empty: the finalization and immutability machinery
treats `value_checksum` as identity, so two sessions with different
`extra_float_digits` would otherwise write two different checksums for one row.
A `search_path`-only revision would have left this behind while appearing to
have hardened these functions.

`strategy_label_bundle_checksum` was audited in the same pass and carries **no**
live defect: it hashes a `jsonb` export, `jsonb` renders date/time values as
ISO-8601 regardless of `DateStyle` (measured), the bundle has no
`interval`-typed field, and the one axis that does reach it --
`extra_float_digits`, via the `double precision` outcome and evidence values --
was already pinned. `DateStyle` and `IntervalStyle` are added anyway, to it and
to `export_strategy_label_bundle` (the function that actually renders, and which
the trainer calls directly to hash its exact export text), so the
governed-checksum family is uniform and a later bundle field cannot
reintroduce the defect silently.

Unlike the data-bearing revisions this one has a real `downgrade()`: a
`proconfig` change is exactly reversible and destroys no evidence. Verified --
downgrading a `0017` database yields a routine fingerprint byte-identical to a
pristine `0016` build across all 93 routines. Reversing it does reinstate both
defects, including a schema no unattended `pg_restore` can load.

`20260803_0018` retires the forecast governance enforcement layer and the
hindcast plane. It implements Option 4 of
`../plans/checksum-layer-audit-2026-08-03.md` (§6), extended by the owner with
the hindcast plane and three of the four locked owner roles: 48 triggers, 34
routines, 2 tables, 1 view, 10 status-to-checksum evidence CHECKs and 3 roles
are dropped, one generated column becomes plain storage, one DEFAULT flips, and
one procedure is amended.

The audit's finding in one line: the **checksum columns** earn their keep and
stay, the **database-level enforcement built on top of them** does not. §3.1 is
the reason — tamper-evidence needs the digest to be beyond the reach of the
party who might tamper, and here the researcher, the DBA and the hypothetical
adversary are one person with one credential. The digest sits in the same row it
protects, in the same database, defended only by triggers the table owner can
disable; the repo already ships two copy-pasteable bypasses
(`…0014:169`, `tests/test_forecasting_v1_upgrade_postgresql.py:209`). What
remains is accident prevention, and §3.2 shows that is already delivered by the
checksum columns plus `materialize_forecast_iteration`'s idempotency block, both
retained. All 61 checksum columns and their 29 format CHECKs stay.

The consequence for every later revision: **checksums are records, not
enforcement; there are no database-enforced state machines; the CLI owns
preconditions.** Do not write a migration that reinstates a `verify_*` /
`guard_*` / `enforce_*` / `finalize_*` trigger on the forecast, strategy,
release-set or intervention planes without reopening that audit.

What survives, and why each survivor is load-bearing, is enumerated in
`../db/AGENTS.md` (*What survives `0018`*): the retained
`ck_forecast_receipt_finalized_evidence` (the only remaining bar to
`status = 'finalized'` on an evidence-free receipt, which
`v_forecast_series_serving:61` trusts); the seven `record_*` writers (whose
`forecast_input_recorded_at` rows `v_forecast_timeseries_contract` INNER JOINs
and `forecast_daily_bootstrap` RAISEs on); `guard_strategy_review_change` (sole
populator of the two strategy checksum columns); `guard_forecast_immutable_rows`;
the `require_*` family; the four `publish_*`/`validate_*` movers; and
`plantgeo_forecast_mv_refresh_owner`, kept because it owns the ML matview and
its `SECURITY DEFINER` refresher and the two must remain the same role. **That
last survivor did not survive `20260808_0019`** — see the section below; the
matview and refresher are reassigned rather than left with a dedicated owner.

Three things this revision changes that no DDL signature reveals:

- `forecast_iteration_value.value_checksum` stops being `GENERATED ... STORED`,
  so `materialize_forecast_iteration` is forward-loaded to compute it
  explicitly. Without that amendment every new row would be NULL,
  `forecast_iteration_receipt_checksum`'s `string_agg` would return NULL, and
  `concat_ws` would silently omit it — yielding a well-formed 64-hex receipt
  digest that covers nothing and passes every retained format CHECK.
- `agri.finalize_forecast_receipt` is gone and was the **only** writer of
  `forecast_receipt.receipt_checksum`; no `forecast_receipt_checksum()` function
  exists. The ML serving view cannot gain a new row until a Python publisher
  reproduces that digest byte-exactly under the `20260803_0017` determinism pins.
- Dropping `ck_forecast_publication_published_evidence` and
  `ck_forecast_training_validated_evidence` lets `v_forecast_series_serving`
  serve NULL manifest/publication/training checksums, and flipping
  `forecast_iteration.purpose`'s DEFAULT to `'serving'` changes the receipt
  digest for new iterations because that column is hashed. Pre-`0018` rows keep
  `'evaluation_only'` and re-derive exactly as before.

Conventions this revision sets for every later teardown: **no `DROP ... CASCADE`**
(name every object, order by dependency, let a missed dependency fail loudly),
full argument-type signatures on every `DROP FUNCTION`, and role teardown as
`REASSIGN OWNED BY … TO CURRENT_USER` then `DROP OWNED BY` then a `DROP ROLE`
that traps only `dependent_objects_still_exist` — roles are cluster-wide while
both `OWNED BY` statements are database-local, so a role still holding objects in
a database that has not replayed this revision survives with a NOTICE. Note also
that roles, ownership and every `GRANT`/`REVOKE` are **invisible** to the parity
test: `dump_schema.DUMP_ARGS` uses `--no-owner --no-privileges`, so a role drop
produces no diff in `../db/agri/**` and a forgotten `REVOKE` is not caught.

Like the data-bearing revisions, `0018` has no `downgrade()`: reversing it would
have to invent the dropped hindcast rows and digests only the dropped finalizers
could compute. Restore a verified backup into a fresh database.

## `20260808_0019` retires the forecast capability-role family

`0018` cut the role model down to one survivor; `0019` finishes it. All five —
`plantgeo_forecast_writer`, `_publisher`, `_reader`, `_mv_refresher` and
`_mv_refresh_owner` — are dropped, and applications connect with the single owner
credential. This is the 2026-08-03 "no custom DB roles" decision applied to the
family that predated it, after the morning's per-lane-trainer-role question was
answered "no" and then superseded by "the family itself goes"
(`../../../docs/reports/migration-decision-packet-2026-08-08.md` § Resolution).

The evidence, verified on a head-migrated database before the revision was
written: the four capability roles are `NOLOGIN` bundles with **zero members**,
no DSN authenticates as any of them, and all four **lack `USAGE` on schema
`agri`** — they reached it only through the owner roles `0018` retired, so every
grant they still held has been unreachable since. Separation of duties needs two
parties; here the researcher, the operator and the DBA are one credential, which
is the same finding the checksum audit made about the tamper-evidence triggers.

The teardown is per-role and reuses `0018`'s `_RETIRE_OWNER_ROLE` shape exactly:
`REASSIGN OWNED BY … TO CURRENT_USER`, then `DROP OWNED BY`, then `DROP ROLE`
with the `dependent_objects_still_exist` NOTICE trap, all inside an existence
guard. The reassignment is load-bearing for exactly one role —
`plantgeo_forecast_mv_refresh_owner` owns `mv_forecast_ml_daily_serving`, its
unique index and `refresh_forecast_ml_daily_serving()`, so a bare `DROP OWNED BY`
would **delete the ML matview** — and is applied to all five so that an
unexpected object in an unseen database is handed over rather than dropped. No
table, function, view or index is otherwise touched; the ML data plane is intact.

Three consequences worth naming. `refresh_forecast_ml_daily_serving()` stays
`SECURITY DEFINER` but its definer is now the calling owner credential, so the
bit is inert; matview ownership — which a non-concurrent `REFRESH` requires — is
preserved by the reassignment. `agri_data_service.cli` drops its
`SET LOCAL ROLE plantgeo_forecast_mv_refresher` and the catalog eligibility probe
behind it in the same commit, because the role it assumed no longer exists. And
the `20260802_0016` `REVOKE EXECUTE … FROM PUBLIC` on the covariate functions is
deliberately **not** re-granted: under one credential the caller is the owner, so
those functions stay owner-callable, which is everyone who connects.

Like `0018`, this revision is invisible to the parity test — `--no-owner
--no-privileges` means a role drop and an ownership change produce no diff in
`../db/agri/**`, so regenerating the declarative tree after it is a no-op. That
also means nothing in the parity harness will notice if the family is re-created.
`tests/test_security_definer_lockdown_postgresql.py` carries that assertion
instead: no `plantgeo_forecast_*` role may exist, and no `agri` function may be
owned by one.

No `downgrade()`. Recreating five `NOLOGIN` roles is trivial; reconstructing the
grant matrix they accumulated across `0014`, `0015`, `0016` and `0018` is not,
and the readiness contract that consumed that matrix is deleted in the same
commit. Roll back by deploying the previous build.

## PostgreSQL 18 portability (rehearsed, not deployed)

Revisions `0001` through `0016` were applied end to end against PostgreSQL 18.4
(`timescale/timescaledb-ha:pg18`) on 2026-08-02 with no error, no revision
change, and no schema change. The full behavioural contract suite passes on that
server, and the object inventory, routine bodies, `SECURITY DEFINER` flags,
`proconfig` GUC pins, pgcrypto digests and pinned float/date/interval rendering
are byte-identical to PostgreSQL 16.14. No revision needs a pg18 variant.

**That is a statement about DDL portability only, and it was read too broadly.**
This section originally concluded "no `0017` is required". A later
populated-restore rehearsal found two defects a DDL-only rehearsal structurally
cannot see, and `20260803_0017` carries both (above). Neither is a PostgreSQL 18
problem -- they reproduce identically on 16 -- but the restore one is what makes
"keep pg16 alive as the rollback" executable, so it gates the migration anyway.

The rehearsal predates `0017`, `20260803_0018` and `20260808_0019`; none has been
replayed on pg18. The two role teardowns are the ones to re-rehearse there,
because they are cluster-wide: the pg18 rehearsal database `plantgeo_boise_pg18`
still sits at `20260802_0016` and holds objects owned by the retired roles, so
every `DROP ROLE` takes the trapped-NOTICE path until that database replays both
revisions too.

The one genuine catalogue difference is that PostgreSQL 18 stores NOT NULL as
`pg_constraint` rows and 16 does not. That is a server-version artifact, not
something a migration controls, and it is handled in the parity tooling rather
than in schema. See `../db/AGENTS.md` (*Toolchain and major-version awareness*)
and `../plans/postgresql-18-migration-rehearsal-2026-08-02.md`, including what
the rehearsal deliberately did **not** cover (data restore, and Railway itself).
