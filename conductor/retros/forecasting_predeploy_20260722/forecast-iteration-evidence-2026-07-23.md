---
type: evidence
---

# Generic forecast-iteration continuation evidence

Revision `20260723_0010` was applied only to disposable database
`plantgeo_geospatial_test_20260723_1421`. The persistent local `plantgeo`
database, Railway, schedules, operational publications, strategy selection,
and recommendations were not changed.

The canonical pipeline is:

`v_forecast_timeseries_contract` -> release/as-of contract -> UTC date spine ->
resolution-aware daily alignment -> deterministic historical-increment
bootstrap -> immutable iteration values -> complete-bucket actual reconciliation
-> outcome and ML-signal views.

The scale-safe server high-water ledger contains 3,203 contract, release-set,
and source-release rows (592 KB), rather than one row per approximately
35 million observations. Local developer/loader/viewer capabilities cannot
mutate it. Iterations persist their canonical contract and training license
snapshots. Actual digest v2 persists and binds the later release-set identity,
manifest, observation checksums, and source-release license snapshots.

Retained-series proof:

- series: `ee98ea66-e9e9-4997-85da-d5e79d443a23`;
- release set: `10f6933b-c048-4dbc-9c33-68e00d2e6d87`;
- iteration: `46e7673a-b263-4922-8e8c-4e01fdb555dd`;
- key: `nasa-power-ws2m-20260331-bootstrap-v2`;
- 1,432 training days, 1,431 increments, 1,000 simulations, 30-day horizon;
- 30 low/median/high values and 30 license-bound actual-v2 values;
- receipt:
  `bafa2af9ef8cfeb39ad5025c9a8118b7ca23d4d1f4e36591ee834a25c15008cb`;
- MAE `0.7916666667`, RMSE `0.8980079807`, empirical p10-p90 coverage
  `0.9666666667`.

The pre-existing 14 v1 and 14 v2 hindcast receipts retained aggregate checksums
`5c27c4f71db62fa78b21b93d27311afaae1bf949bc86a7a28754a3d041bb689d`
and
`afa66be603ac97d92bcb232ce7b02da6c6c507eefd72e803d251a36efe041fd1`.
Operational forecast receipts, values, publications, and pointers remain zero.

Independent review reported no remaining P0-P2 findings. The final integrated
validation sweep completed on 2026-07-23:

- Ruff formatting and lint: passed across 93 Python files;
- mypy: passed across 46 source files;
- pytest: 203 passed, 6 opt-in database tests skipped; the new disposable-clone
  PostgreSQL iteration/reconciliation proof ran and passed;
- ESLint: zero errors and 75 pre-existing warnings;
- TypeScript, client data-boundary, and restricted-import checks: passed;
- Vitest: 140 passed across 38 files;
- Next.js production build: passed.

This is framework evidence, not an operational or life-safety forecast. The
input remains a stale 55.66-km Denver point series, and method v1 does not model
seasonality, autocorrelation, regimes, current forecast weather, or
cross-series dependence.
