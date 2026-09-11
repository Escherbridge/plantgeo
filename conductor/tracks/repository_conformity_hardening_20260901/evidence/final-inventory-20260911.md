---
type: evidence
slug: repository_conformity_hardening_20260901
wave: c0-c4
date: 2026-09-11
source_revision: fa202230958fb55521963e886eb031be5fc266c4
status: independently-approved-owner-handoffs-pending
---

# September 11 repository-conformity inventory

This is a fresh checkout-level inventory at `fa20223`, not a replay of the September 2 scan.
It applies the track's proof-before-delete contract after the September 10 snapshot-dispatch
cleanup and September 11 MTBS rollout. It neither probes nor mutates production. Existing
uncommitted registry/runbook/session-maintenance changes belong to other owners and are excluded
from this track's write set.

## Classification rules

- **Immediate** means a present safety or deploy-gate defect this track owns and can fix now.
- **Confirmed** means the implementation debt or orphan status is proven on the current tree;
  already-landed removals stay in this class and are not recreated for another deletion pass.
- **Contingent** means the static candidate is real but deletion or refactoring awaits a named
  runtime, policy, contract-parity, or current-owner gate.
- **False positive** means the candidate has a live consumer, supported operational role, or
  transitive dependency reason that invalidates the proposed cleanup.
- **Forbidden evidence** means imports are deliberately irrelevant: migration or executed
  reproduction history must remain available unless its owning governance record retires it.

## Current verdicts

| Candidate | Classification | September 11 evidence and disposition |
| --- | --- | --- |
| Moderation causal-benefit scorecard | **Confirmed, already removed** | `2b4cfef` removed the displayed placeholder and `ad4e015` removed the submission default. No new immediate safety candidate was found. Do not restore a score without provenance-carrying evaluation evidence. |
| WebGPU accelerator, unused layer worker, ambient WebGPU types, six unused UI modules, and `whichnull.py` | **Confirmed, already removed** | All ten named source paths are absent. A repository scan outside Conductor/PLAYBOOK finds only the retained explanatory note in `LayerManager.tsx`; there is no runtime import to resurrect. The September 2 TypeScript/Python proof packets remain the deletion receipts. |
| `@deck.gl/mapbox`, `@deck.gl/react`, `jotai`, Python `s3fs`, and Python `redis` | **Confirmed, already removed** | None is present in the direct manifests or locked graphs. Root `preact` remains exact-pinned; the Python realtime path still speaks RESP without the removed client package. Lockfile and image/quality checks belong to the integrated receipt below, not a new removal. |
| Snapshot HTTP/CLI dispatch and unused MTBS readers | **Confirmed, already removed** | `9b239fa` and `3632d61` are preserved in the completed-cutover retrospective. `SNAPSHOT_PRODUCTS` is intentionally empty for public serving while `FROZEN_SNAPSHOT_PRODUCTS` and explicit recovery APIs remain. Empty public dispatch is not authority to delete frozen provenance readers. |
| Thin CLI adapter boundary | **Confirmed structural debt, partitioned by owner** | A fresh scan found 30 exact sites in `interface/cli/commands.py`: 24 transaction boundaries plus all six chunked-lane framework types. The executable guard saw only 26 because it missed `LaneChunk`, `LaneReceipt`, `LanePlan`, and `LaneCheckpoint`. C2 owns extraction of all six framework types and the six forecast transactions. The other 18 transaction sites belong to current executor/historical/product owners and are handed off without editing. Preserve the five-family `forecast`/`ml`/`data`/`ops`/`agent` command contract. |
| Snapshot schema/column ownership | **False-positive owner absence; contingent reader split** | `warehouse.parquet.snapshot_signal_product` already owns the canonical Arrow fields, grains, aggregations, and registrations. `parquet_ops.snapshot_products` separately spells reader projections and remains a genuine decomposition candidate, but reader R2 owns it and offline export serializes it. Record the proposed split and derive projections from the registered descriptor only after those owners hand it off. |
| Canonical snapshot-builder receipt/finalization consolidation | **Contingent — offline-builder owner** | The four builder scripts and all offline staging/build/upload machinery are frozen by the current ownership directive. This track records the duplication and may provide canonical schema ownership outside those files, but it cannot claim byte-for-byte builder consolidation or edit the builders. Hand off with the exact golden-byte/receipt/checkpoint/manifest/`_COMPLETE` acceptance gate. |
| `teams.inviteMember` compatibility procedure | **Contingent — telemetry and sunset** | It has no repository caller beyond the authorization-order compatibility test, but remains a deployed API surface. The code and API reference name `createInvitation` as replacement and sunset `2026-10-01`. Delete it and its single compatibility case only after production request telemetry is checked at or after that gate. |
| `src/lib/server/services/geofence.ts` | **Contingent — product-owner decision** | `checkGeofences` has zero repository consumers, but it is unlisted in this track's owned C3 files and contains the only enter/exit alert transition behavior while the tracking router still manages geofences. `docs/services.md` also claims a differently named, richer geofence-monitoring service. The owner must choose wiring a canonical implementation or deleting the orphan and reconciling the service contract; a zero-import scan alone cannot choose. |
| `agri_data_service.planes` and its serving tests | **Contingent — contract parity** | Production imports remain absent, but the package still owns point-in-polygon, HUC12/mukey lookup, severity selection, as-of release resolution, evacuation coverage classification, and sensor month coverage. Current lane/direct documentation also names several plane behaviors. Keep the package and its twelve serving suites until a live lower-level API proves those behaviors or an owner explicitly retires them. |
| `execution.hot_projection` and its contract tests | **Contingent — live policy** | `docs/historical-backfill-runbook.md` still requires the bounded Railway hot projection and `docs/sql-forecasting-framework.md` still distinguishes its selection contract from evidence. Retire those current policies in their owning lane before deleting their only typed implementation. |
| `places.ts` | **False positive** | The September 2 zero-consumer claim was corrected: `placesRouter` imports every reader, the root tRPC router mounts it, and the spatial predicates were implemented. The absent `geo.poi` producer is product debt, not proof that the mounted public API module is dead. |
| Root `preact` dependency | **False positive** | The exact `10.11.3` pin is retained for the NextAuth/Auth.js dependency family. A zero direct-import result does not establish that a peer/transitive runtime pin is removable. |
| Service-local Compose file and Redis container | **False positive** | The service README still defines the HTTP-only development stack, distinct from the root stack and local warehouse; realtime ingest opens the Redis endpoint directly. Removing a Python package did not retire the backing service. |
| `execution.public_evaluation_lineage` and its test | **Forbidden evidence** | The 2026-08-14 production lineage receipt names the module as the successful client-side reproduction path for persisted source/release/set/artifact rows. Preserve it with the historical result until that record's owner supplies an explicit retirement replacement. |
| Drizzle and Alembic migrations/history | **Forbidden evidence** | There are 35 tracked SQL/revision files in the scanned migration surfaces. Unregistered, dormant, hand-applied, or zero-import state is not deletion authority. C4 updates only this track's evidence; migration files, the journal, migration contract, registry, and runbook remain frozen. |
| Generic availability/gap/scheduler registries, product repairs, frontend readers/renderers, and offline construction | **Contingent — active owner lanes** | These files are expressly frozen. Findings are handed to gapless publication, environmental retirement, reader/multiscale acceptance, or offline export rather than edited here. Their outstanding production gates cannot be converted into a conformity completion claim. |

## Reproducible checkout checks

The inventory used repository-wide path/basename/export scans, `git log -S` for compatibility
surfaces, manifest/lock searches, active-track metadata, and the executable import-lattice AST
guard. Notable current-tree results:

- all ten previously removed source paths return `Test-Path = False`;
- outside historical docs, the removed WebGPU/worker names appear only in the retained
  `LayerManager.tsx` incident explanation;
- `inviteMember` has no source caller outside its router and one security compatibility test;
- `checkGeofences` is defined once and imported nowhere;
- `hot_projection` and `public_evaluation_lineage` are imported only by their contract tests, but
  the current policy and executed historical receipts above are independent retention evidence;
- all production imports of `agri_data_service.planes` remain absent, while twelve plane serving
  suites and direct/lane documentation still pin behavior;
- `SNAPSHOT_PRODUCTS` is empty, but the frozen catalogue and explicit recovery contract remain.

## Implemented bounded closure

- `execution/forecast_workflows.py` now owns the six approved forecast transactions, SQL loading,
  domain calls and result payloads. Their existing Click helpers retain configuration lookup,
  timestamp/option validation, exception translation and output only.
- The four forecast query resources moved from `sql/cli/` to `sql/execution/` with owner headers
  naming `execution.forecast_workflows`. The pre-existing vegetation reconciliation query remains
  byte-for-byte unchanged under its existing execution owner; the command-specific variant has a
  distinct constant and resource basename.
- `execution/chunked_lane.py` owns the six reusable lane protocols/classes and returns native
  payloads or a typed native failure. The adapter binds product functions and translates failures
  to the established Click contract.
- The thin-adapter guard now recognizes all `Lane*` protocol roles. It separately proves the six
  forecast helpers contain no transaction/SQL calls and pins the exact 18 current-owner
  transaction sites under the existing strict xfail.
- `warehouse.parquet.snapshot_signal_product` publishes typed schema descriptors beside the
  canonical registrations. Reader projection derivation remains a reader-owner handoff; no
  frozen `parquet_ops` or builder file was edited.
- Interface documentation now records all five command families, including `agent`.

## Rollback and handoff rule

The implementation commit is rolled back as one commit; it performs no data, migration, service,
or production mutation. Owner-frozen candidates are not copied into this track. Their handoff is
the table row above plus the owning track and exact acceptance condition. Historical migrations,
receipts, and reproduction modules are retained in place, so rollback never depends on reconstructing
deleted evidence.

## Final verification and independent review

The verification sweep ran on the complete local edit batch. Initial sandbox-only failures were
retried with host access when npm/uv/Podman needed their normal caches, managed runtime, network,
or WSL socket. The first Python sweep exposed moved-test imports and Ruff formatting; those tests
now import the helpers from their new execution owner, and the full gate was rerun after the fix.

| Gate | Result |
| --- | --- |
| `npm run check:data-boundary` | **PASS** — 12 documented URL rules, restricted imports and observation-fabrication checks passed. |
| `npm run type-check` | **PASS**. |
| `npm run lint` | **PASS with disclosure** — zero errors and 542 existing warnings, dominated by vendored/minified `static/datastar.js` and React compiler diagnostics outside this write set. |
| `npm test` | **PASS** — six tooling tests plus 2,151 Vitest tests passed; 13 database tests skipped because `PLANTGEO_TEST_DATABASE_URL` and `POSTGIS_TEST_DSN` were absent. |
| `npm run build` | **PASS** after granting the build access to fetch its configured Google Fonts. The middleware-to-proxy deprecation warning remains outside this track. |
| `UV_NO_SYNC=1 uv run --no-sync python scripts/check.py` | **PASS** — format 0.12s, Ruff 0.13s, mypy 4.47s and full pytest 258.44s. No database DSN was present, so this is not a live-database acceptance claim. |
| Agri service and job-executor Podman builds | **EXPECTED OWNER-HANDOFF REFUSAL** — both reached `verify_quality_receipt.py`; the frozen receipt covers digest `c9739cb7…` over 1,307 files while the pre-review tree held 1,310 files. After the review-required SQL ownership move, a direct verifier rerun records the final digest `7800c519…` over the same 1,310-file domain. `offline_export_service_20260908/metadata.json` lists `QUALITY_RECEIPT.json` as serialized, and environmental retirement requires exactly one end-of-wave writer, so this lane did not rewrite or bypass it. |

The review-required SQL ownership correction then passed a narrow revalidation: Ruff format and
lint, mypy over both affected source modules, and 47 forecast/CLI/vegetation/layer/SQL-loader tests
passed with the single intentional thin-CLI xfail. The whole-suite results above predate only this
path/header move; query text and runtime behavior were unchanged.

No deployment, image push, database access, migration, service mutation or production probe was
performed.

The independent review initially blocked approval because the four moved forecast queries still
lived under `sql/cli/` and named the old interface owner. The resources and their ownership headers
were moved to `sql/execution/`; the already-existing vegetation reconciliation query stayed
byte-for-byte unchanged, while the command variant received a distinct resource/constant name.
After the focused revalidation above, the reviewer returned **APPROVE** with no remaining security,
correctness, performance, maintainability, test-coverage or ownership blocker for this bounded
commit. The reviewer also confirmed that frozen reader/builder/registry/migration/receipt files
were not edited. Overall track status remains `active` until the recorded owners close those gates.
