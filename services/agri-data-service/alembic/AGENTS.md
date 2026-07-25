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
