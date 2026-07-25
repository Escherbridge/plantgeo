---
type: retrospective
---

# Local forecast and predeploy retrospective — 2026-07-22

## Outcome

The forecasting framework now retains immutable historical projections and their
later actuals as a governed ML-signal plane, but the first candidate was correctly
rejected and nothing was published. This is a useful result: the system preserved
negative validation evidence instead of manufacturing a production success.

The selected series was NASA POWER daily WS2M at 40 N, 105 W: 1,462 consecutive
point-sample observations from 2022-04-30 through 2026-04-30, native source
resolution 55,660 m, and no gap greater than one day. It is the simplest defensible
candidate because it is scalar, daily, continuous, release-pinned, and long enough
for deterministic rolling-origin evaluation. It is regional context, not an
acre-scale claim.

## Forecast evidence

- Release set: `10f6933b-c048-4dbc-9c33-68e00d2e6d87`
- Release manifest: `7586ea8eae24f9e1e6adfc26f42bb89e2abbe3066be119e63536dbfa1fb8e6fc`
- Source release: `31486536-de3b-4e54-989e-50ba5248fc83`
- Source data: `fb4902f5-0ea4-49af-8924-7ff039bd3f5f`
- Spatial cell: `1d5f67c7-9c4f-4755-bc68-3895b0d55ce9`
- Rejected forecast run: `598466ea-1181-4772-8f30-f46574dce1e9`
- Terminal seven-day RMSE: `0.6248889317663759`; naive RMSE: `1.6827783149134221`; skill: `0.6286564152696927`
- Fourteen-origin aggregate RMSE: `1.0076008330692794`; naive RMSE: `1.3054340899736403`; skill: `0.2281488274221296`
- Only 3 of 14 origins passed; empirical interval coverage was `0.6326530612244898`.
- Immutable outcome plane: 14 finalized hindcast receipts and 98 typed signal rows.
- Canonical receipt manifest: `21f5c127e0084ec1f7501c096c90169e57487bec514ec57cfa43c279c38c40e8`
- Operational publication/receipt/value counts after evaluation: 0/0/0.

## What worked

- Separating hindcast time from operational issue time avoided backdating production
  forecasts and made forecast-versus-actual outcomes safe future training features.
- Database finalization recomputes regression, calibration bands, naive comparisons,
  actual lineage, metrics, and receipt checksums instead of trusting client summaries.
- The terminal holdout looked strong, while the broader origins exposed instability.
  Requiring rolling-origin evidence prevented a misleading publication.
- Capability roles, published-only serving, and explicit refresh make the future
  deployment boundary reviewable before infrastructure is touched.
- Independent review found real shortcomings in the first fixture and digest contract;
  those findings were retained as blockers rather than hidden.

## What did not work cleanly

- The first SQL execution timed out and rolled back; the next exposed a PL/pgSQL name
  ambiguity and also rolled back. The optimized third execution committed.
- Live v1 used a hard-coded planned chronology earlier than its actual row creation.
  The source fixture now uses database statement time, but the rejected live evidence
  remains unchanged and must not be presented as if it came from the corrected source.
- Live terminal `metric.passed` is false because aggregate rejection occurred before
  the original terminal validation update. A separate audit note records that the
  terminal thresholds alone were satisfied.
- The original gate did not require minimum origin pass fraction or interval coverage.
  The corrected source now requires 50 percent and 70 percent respectively; live v1
  would fail both and has not been re-executed.
- A generic `job_output` audit note is mutable. Immutable hindcast receipts and their
  value checksums, not that note, are the source of truth.

## Backup and local access evidence

The verified pre-0006 restore point is
`C:\tmp\plantgeo-warehouse-backups\plantgeo-pre-0006-20260722-complete.dump`,
1,528,682,576 bytes, SHA-256
`86515C6F633B9E86E3241822FB233C43FB68FB277691B18B60F1BD7D669CAB6A`,
with 487 `pg_restore --list` entries. The similarly named dump without `-complete`
is incomplete and must not be used for recovery.

The local warehouse remains loopback-only at `127.0.0.1:5442`, database `plantgeo`.
PgAdmin viewer and developer credentials live only in ignored
`infra/local-warehouse/.env`; tracked `.env.example` contains placeholders. The
viewer is read-only, the developer is broad but non-superuser, and production-style
forecast capability roles remain least-privilege.

## Validation evidence

- Python integrated run: 164 collected, 160 passed, 2 skipped, 2 forecast CLI tests
  failed because they patched a Pydantic settings instance. The tests were corrected;
  focused forecast/readiness/migration regressions then passed, followed by 10/10 route
  and refresh tests after a Pydantic runtime-annotation fix.
- Ruff formatting: 76 files formatted; Ruff lint: all checks passed.
- Mypy: 43 source files, no issues.
- Web typecheck and both data-boundary guards passed.
- Vitest: 38 files, 140 tests passed.
- ESLint: zero errors and 75 unrelated existing warnings.
- Next.js production build compiled, typechecked, generated all 18 static pages, and exited successfully.

## Remaining production blockers

The continuation completed the clean PostgreSQL 16 rehearsal, active-policy and
configured calibration enforcement, versioned stronger receipt binding, and separate
receiver/writer and published-reader service profiles. It also proved a structural
`hindcast_v1` row keeps its exact stored and recomputed checksum across `0007` to
`0008`; this is not evidence that the 14 live receipts were upgraded. Final independent
review additionally required and verified database guards that reject direct-finalized
or explicit-v1 inserts, bind the canonical policy definition, and freeze any policy
referenced by a finalized receipt.

PostgreSQL 18 extension/restore parity remains unproven because no PostgreSQL 18 target
or Railway CLI was available. A post-receipt clone containing the 14 canonical live
receipts is still required for their governed upgrade proof. The corrected v2 fixture
was not executed after a bounded restore attempt stalled, rolled back empty, and was
discarded. Consequently the 50 percent origin-pass and 70 percent interval-coverage
gates have no new real-history result and nothing was published. No Railway deployment,
schedule, forecast publication, or strategy recommendation is authorized until those
gates pass.
