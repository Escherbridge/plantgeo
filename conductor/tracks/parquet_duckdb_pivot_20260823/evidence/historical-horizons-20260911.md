---
type: track-evidence
date: 2026-09-11
status: active
---

# Retained loading-task horizon reconciliation

The five retained tasks were read through their complete available turn pages
on September 11 (`hasMore=false`). The independent read-only analyst
`/root/historical_horizons` compared their claims with the dated repository
receipts below. No new production or object-store measurement was made.
Task-reported completion is labeled separately from later physical census and
current publication/reader acceptance. An absent hash is not replaced by a
different snapshot's hash.

| Exact task title and stable ID | Original scope | Bounded evidence | Remaining evidence |
| --- | --- | --- | --- |
| Complete Soil Wetness Parquet Load — `01a03c1f-66e3-77f1-9cff-5281d6295a25` | PostgreSQL plus deepest NASA POWER GWETROOT source history; concrete target 1981-01-01–2026-08-20, 397 cells, dedicated directory and browser acceptance before removal. | Task reports 16,668 days at four rungs, 6,617,196 base rows and 397 source receipts under `climate-field-soil-wetness-root-zone`; overlap/parity was still running. No final lane manifest hash or browser receipt was recorded. | Map that original prefix to the current `soil-wetness-root-zone` product. The September 7 census's zero live objects for the latter slug is not evidence that the original differently named objects disappeared. Full source-history parity, current availability and reader proof remain open. |
| Complete precipitation parquet load — `01a03c19-9b72-7571-b80f-8969b4a28eda` | PostgreSQL plus deepest PRECTOTCORR source history, targeting 1981-01-01–2026-08-06; dedicated prefix and browser-before-drop required. | Task proves 2022-04-30–2026-08-06 overlap: 1,560 days, 619,320 logical rows across 397 cells from 1,166,676 physical PostgreSQL rows; legacy signal z13 audit reports zero mismatch days/duplicate grains. No dedicated-prefix completion/hash is recorded. | The task handed sole-writer/read-model work to a second precipitation task whose final receipt is not in this retained record. Obtain that handoff and actual dedicated-prefix history/rung/parity evidence, especially 1981-01-01–2022-04-29. |
| Complete Dew Point parquet load — `01a03c17-a66c-7ac1-9832-91c27d4f00fd` | PostgreSQL plus deepest T2MDEW history, 1981-01-01–2026-08-06, dedicated `climate-field-dew-point` and browser-before-drop. | Task reports 16,654 days, 6,611,638 z13 rows and complete four-rung ladders. The September 7 census independently records that contiguous physical window at all four rungs. | Final full-load value/source-receipt parity and current availability/browser acceptance are absent from this task's last progress. A separate 691-object snapshot hash is not the hash of the entire 1981–2026 live-prefix load. |
| Complete burn-severity Parquet load — `01a03c0b-7fad-7672-acb6-1188d15d90a3` | Every PostgreSQL-held row/release; a later request adds cutover/removal and deeper source backfill if possible. No exact deeper floor was established there. | Task reports exact 23-column and WKB parity for five irregular releases, 541 rows at every rung: 2020-11-24 (150), 2021-09-27 (63), 2022-04-28 (100), 2023-08-09 (115), 2024-08-22 (113); zero remaining/failures and separate as-of repair approval. No content manifest hash was recorded in the retrieved receipt. | This is the five-release PostgreSQL corpus, not full upstream MTBS history. Current 747-fire publication and older 1984–2017 recovery are distinct successor scopes; use their later receipts. Do not convert fire years into invented publication dates. |
| Complete drought parquet load — `01a03c0b-298f-7a73-b6e5-0b4dd5ba83f3` | PostgreSQL history, followed by explicit client cutover/removal and deeper source backfill if possible. The later loader can reach USDM 2000-01-04 but was explicitly not executed in that task. | Task reports 209 releases/1,045 rows, 2022-08-09–2026-08-18, at four rungs; exact grains and non-geometry fields match, zero missing/extra rows, two governed absences do not overlap data, zero drain failures/remaining and separate approval. No content manifest hash was recorded. | 2000-01-04–2022-08-08 is not proved backfilled; archive capability is not publication. Geometry equality is outside the task's parity claim. Current browser, forward behavior and retirement require successor evidence. |

## Repository evidence and identity limits

- The [September 7 snapshot/live-prefix census](../../environmental_postgres_retirement_20260904/evidence/snapshot-products-vs-availability-20260907.md)
  distinguishes frozen snapshot history from live prefixes and independently
  records dew point's 16,654-day four-rung physical ladder.
- The [September 6 coverage investigation](../../environmental_postgres_retirement_20260904/evidence/absent-coverage-lanes-20260906.md)
  records the separate 691-object dew-point snapshot build with SHA-256
  `c2972ea61ebfb66a86fa1e834625fae163e5d0a0abfd39f8c701edca3e59b71a`.
  This receipt does not bind the entire earlier-source live-prefix load.
- The historical [drought brief](../../../retros/session_hygiene_20260911/layer-sessions/drought.md)
  separates the measured PostgreSQL floor from USDM archive capability; the
  [burn-severity brief](../../../retros/session_hygiene_20260911/layer-sessions/burn-severity.md)
  preserves irregular source-release dates. Neither is a current execution queue.
- The [September operational retrospective](../../../retros/parquet_operational_checkpoints_20260911/README.md)
  records the later bounded current MTBS rollout and explicitly leaves old MTBS
  history and subsequent scheduled execution open.

## Disposition

The original horizons are reconciled as an inventory. Full-history acceptance
remains open for root-zone and precipitation. Dew-point physical history through
August 6 is evidenced, while final full-load parity and current product acceptance
are pending. Burn severity and drought have task-level PostgreSQL-mirror receipts
with deeper-source and operational obligations preserved separately.

Pivot owns this scope inventory. Gapless receives the historical publication,
prefix/availability, source-direct gap and absence proof dependencies; offline
receives only construction dependencies within its selected-builder remit, with
any scope extension assigned explicitly before work. Reader owns current
selected-day/product/browser acceptance. Environmental retirement retains all
environmental retirement and archive-replacement proof plus the old-MTBS product
repair. New shared-file or product-adapter work is assigned through the
[ownership ledger](integration-ledger-20260911.md) before implementation; this
inventory does not expand a peer's exclusive file set.

No original task or umbrella is marked complete or archived by this reconciliation.
