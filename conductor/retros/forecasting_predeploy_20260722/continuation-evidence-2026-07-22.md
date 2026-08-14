---
type: evidence
---

# Forecasting predeploy continuation evidence — 2026-07-22

## Scope and outcome

The continuation preserved the intentionally dirty checkout, the live NASA POWER and
USDM history, rejected run `598466ea-1181-4772-8f30-f46574dce1e9`, and its canonical
manifest `21f5c127e0084ec1f7501c096c90169e57487bec514ec57cfa43c279c38c40e8`.
It performed no Railway mutation, deployment, scheduling, strategy-selection write,
recommendation write, or forecast publication. The live `plantgeo` warehouse was not
migrated or used for a repeated forecast run.

A final read-only audit of that warehouse reported Alembic `20260722_0007`, exactly
14 finalized hindcasts and 98 hindcast values for the rejected run, and zero rows in
each of `forecast_publication`, `forecast_receipt`, and `forecast_value`.

## PostgreSQL rehearsals

The first explicit disposable database,
`plantgeo_forecast_test_20260722_continuation`, migrated cleanly through
`20260722_0007`; both representative PostgreSQL forecasting/function tests passed and
the exact database was dropped.

Final validation used two more explicit disposable databases on
`127.0.0.1:5442`, owned by `plantgeo_owner`:

- `plantgeo_forecast_test_20260722_final` migrated from empty through
  `20260722_0008` and ran the full Python suite against PostgreSQL.
- `plantgeo_forecast_test_upgrade_20260722_final` migrated empty through `0007`,
  seeded a structural finalized v1 receipt, then migrated through `0008`.
- Both reported PostgreSQL `16.14`, Alembic `20260722_0008`, 61 `agri` relations,
  `pgcrypto 1.3`, `postgis 3.6.3`, `timescaledb 2.27.0`, and `vector 0.8.2`.
- The two-phase v1 test reported `1 passed, 1 skipped` in 0.52 seconds for the seed
  phase and `1 passed, 1 skipped` in 0.58 seconds for the verify phase.
- The migrated structural v1 row remained finalized with digest version
  `hindcast_v1`; stored checksum
  `62ebb8502d7dfca761d04e1029e1a08365572e2c51b657ed5b4bf426abb7b583`
  exactly matched independent recomputation.

Immediately before disposal, both final databases were re-queried by exact name and
verified as owned by `plantgeo_owner`; each had one other database-scoped session.
`DROP DATABASE <exact identifier> WITH (FORCE)` was issued only for those two names,
and the post-drop catalog count for the exact allowlist was zero.

## Governance changes

Revision `20260722_0008` makes future receipts `hindcast_v2` while preserving the v1
formula for existing rows. The v2 canonical JSON digest binds the declared run,
series, model, policy, and release identities; the complete policy-definition
contract used by finalization; forecast and calibration cutoffs;
horizon, interval, counts, mode, and declared checksums; plus each horizon value's
time, actual-availability timestamp, and value checksum. The finalizer locks an active
policy, enforces its configured residual calibration sample minimum, and derives the
quality result from the locked policy thresholds. Database insert and policy triggers
require every new hindcast to begin as staged v2 and freeze a policy after any
finalized receipt references it. Public execution of `agri` functions is revoked.

Production configuration now requires an explicit `receiver_writer` or
`published_reader` service profile with only its matching DSN. The legacy combined
DSN is confined to the `combined_local` profile. Route mounting, database sessions,
and readiness checks follow the selected profile; readiness audits exact relation,
sequence, function, ownership, membership, column-grant, and grant-option boundaries.

## Independent-review closure rehearsal

After review identified the direct-insert and mutable-policy-definition bypasses, two
fresh databases were created: `plantgeo_forecast_test_20260722_reviewfix` and
`plantgeo_forecast_test_upgrade_20260722_reviewfix`. The main database migrated to
`0008`; the upgrade database migrated to `0007`, seeded the structural v1 receipt, and
then migrated to `0008`. The seed phase reported `1 passed, 1 skipped` in 0.59 seconds
and the verify phase reported `1 passed, 1 skipped` in 0.61 seconds.

Both targets used PostgreSQL `16.14` with `pgcrypto 1.3`, `postgis 3.6.3`,
`timescaledb 2.27.0`, and `vector 0.8.2`. Catalog evidence showed all three governance
triggers: `forecast_hindcast_insert_contract`,
`forecast_hindcast_finalization_policy_guard`, and
`forecast_quality_policy_finalized_receipt_guard`. The outcome view exposed
`receipt_digest_version`, `quality_policy_id`, `policy_key`, and
`quality_policy_contract`. The upgraded structural v1 row remained finalized with the
same `62ebb850...abb7b583` checksum and exact SQL recomputation equality.

Immediately before review-fix disposal, both exact names were again verified as owned
by `plantgeo_owner`; each had one other database-scoped session. Database-scoped
`WITH (FORCE)` disposal was issued only for those two exact identifiers, and the final
catalog count for that allowlist was zero. The exact pytest temp directory created for
this pass was also removed and verified absent.

## Integrated validation

- Ruff format check: 80 files already formatted.
- Ruff lint: all checks passed.
- Mypy: no issues in 43 source files.
- Pytest: 179 collected; 177 passed and 2 intentionally phase-gated tests skipped in
  97.61 seconds.
- TypeScript typecheck: passed.
- Client data-boundary guard: 11 documented URL rules passed; restricted imports
  passed.
- Vitest: 38 files, 140 tests passed in 320.58 seconds.
- ESLint: zero errors and 75 unrelated existing warnings.
- Next.js 16.2.2 production build: compiled in 2.1 minutes, completed TypeScript in
  58 seconds, and generated all 18 static pages in 3.6 seconds. The existing middleware
  deprecation warning remains.
- Independent review first caught direct-insert and mutable-policy-definition bypasses.
  Both are closed by database triggers and digest/test coverage. The follow-up
  independent review reported no blocking findings and confirmed v1 compatibility and
  honest blocker wording. The earlier validation pass also caught and corrected an
  isolated negative-test fixture association and variable typo.

## Honest blockers

PostgreSQL 18 parity is still blocked: no local PostgreSQL 18 server/client, usable
Podman target, or Railway CLI was available. The verified pre-0006 backup cannot prove
preservation of the later 14 v1 receipts, so a verified post-receipt clone remains
mandatory. The structural v1 test proves checksum/schema compatibility only.

The corrected v2 real-history fixture was not run. A bounded restore attempt into a
disposable database stalled during constraint/index restoration; the verified orphan
backend for only that database was terminated, rollback left the target empty, and the
target was safely dropped. Therefore there is no new governed 14-origin result, the
50 percent origin-pass and 70 percent interval-coverage gates remain unevaluated, and
publication remains unauthorized.
