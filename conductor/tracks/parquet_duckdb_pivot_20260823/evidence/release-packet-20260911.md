---
type: release-packet
date: 2026-09-11
status: blocked
---

# Active-track release and rollback intake

**Decision: HOLD.** No peer candidate has been accepted and no combined release
SHA exists yet. This packet records the concrete scope and evidence needed to
complete owner review. It is not an authorization request for an unspecified
release. Fill the exact candidate and runtime identities from reviewed handoffs
before presenting a production action for approval.

## Candidate identity

| Field | Current value |
| --- | --- |
| Integration branch | `codex/active-track-integration-20260911` |
| Starting commit | `fa202230958fb55521963e886eb031be5fc266c4` |
| Inherited baseline | September maintenance/botanical snapshot; parent-owned and uncommitted, excluded from integration output |
| Accepted peer commits | None; see [intake ledger](integration-ledger-20260911.md) |
| Final combined code commit and tree | Pending accepted branches |
| Python source digest and verified quality receipt | Pending exact combined source domain and full receipt-producing sweep |
| Independent integration review | Pending complete candidate |
| Fresh service/deployment and rollback identities | Pending read-only preflight from the relevant owners |
| Data/forecast certification | Not established; [release governance](../../../release-governance.md) remains authoritative |

## Service matrix and approval scope

The IDs below are the dated inventory in [deployment documentation](../../../../docs/deployment.md#production-boundary).
Confirm identity/configuration and record current deployment SHA/image before
any approved action; these rows are not a fresh deployment census.

| Service | Stable ID | Candidate need and required rollback evidence |
| --- | --- | --- |
| `plantgeo-main` | `fa08a3aa-6d1d-43eb-846b-15dbfd887d61` | Include only if the accepted tree changes web behavior. Verify exact commit, image, full build gates, matching migration contract and `/api/ready`; pin the previous compatible image/commit. |
| `plantgeo-parquet-api` | `33aed861-af76-4fdd-a95e-784bdcc95e55` | Include only if accepted Python changes affect serving. Verify build config, source digest, published-reader profile and `/ready`; pin the previous compatible image/commit and availability contract. |
| `plantgeo-job-executor` | `565ecaad-9946-48f1-8a0b-28fa60494a16` | Include only if accepted scheduler/writer changes require it. Record exact image, effective allowlist, definitions, active leases/runs and preserved checkpoints. Rollback disables affected lanes through supported controls and preserves data/history. |

Code deployment, executor activation/configuration, candidate upload, physical
publication, availability promotion, source correction/retraction, migration and
service/relation removal are separate actions. Each requested action must have
its own exact product/day/source/generation or relation scope, evidence and
recorded authorization. A push to `main` triggers deployment and is therefore a
release action. This integration pass performs no push or production mutation.

## Evidence that must accompany the candidate

| Owner | Required packet |
| --- | --- |
| Reader | Every production product across newest eligible terminal, populated historical and governed-empty/refusal state; required rungs, coverage/day/window/release routes, cold/warm traces, no environmental PostgreSQL fallback, zero historical LIST/data-part operations in capability reads, selected-day agent/spatial/temporal-neighbour results. |
| Multiscale | Only one valid rung paints; counts/sums conserve against detail; bounded features/bytes, `truncated=false`, request-to-paint timing, screenshots and canvas-pixel continuity on desktop/mobile, honest native polygon versus event-cell support. |
| Gapless | Published availability pointer/generation SHA, authoritative rung set, bootstrap receipt and source inventory root, source/terminal/absence provenance, effective executor ownership/cutoff, three consecutive scheduled advances and retry/restart/expired-lease recovery without overlap per activated product. |
| Retirement | Exact candidate pins and preservation/rollback for signal/sensors, distinct soil products and old MTBS; current zero-reader/writer/ownership proof for any proposed removal. Prepared or preserved artifacts remain distinct from published/served data. |
| Offline | Eight-lane source/history/rung manifest, indexed-history verification and measured disk/memory/object/byte/wall-time evidence; humidity 1981–2017 disposition without rebuilding the verified temperature slice. |
| Conformity | Frozen command/package interface, retained/removal proof and final reviewed source changes; no transfer of another lane's files by implication. |
| Integration/acceptance | Reviewed commit manifest; one final combined boundary/type/lint/full frontend/Python sweep; actual skip disclosures; exact deployed-tree equivalence; independent review and unresolved-risk list. |

Temperature history through August 6 and the bounded September 11 MTBS rollout
remain dated inputs from the [operational retrospective](../../../retros/parquet_operational_checkpoints_20260911/README.md).
Neither substitutes for forward intervals, broader history or schedule burn-in.
The eight-lane pause remains unproven until its effective state is freshly read.

## Final local command packet

Run after all accepted code changes are committed and the combined tree is
stable, from the stated working directories. Use the installed locked tool
environment; do not sync or replace dependencies during a verification run.

```powershell
# Repository root: one final full application sweep.
npm run check:data-boundary
npm run type-check
npm run lint
npm test
```

```powershell
# services/agri-data-service: full source-domain receipt.
$env:UV_NO_SYNC = '1'
uv run --no-sync python scripts/check.py --write-receipt
uv run --no-sync python scripts/verify_quality_receipt.py
```

The orchestrator must record and stop on each nonzero exit code rather than
treating the last command as the batch result. If no test database is being
used, remove `AGRI_TEST_DATABASE_URL`, `AGRI_CROSS_MAJOR_DATABASE_URL`,
`PLANTGEO_TEST_DATABASE_URL` and `POSTGIS_TEST_DSN` from the child environment;
do not set them to empty strings. Disclose resulting integration skips and do
not claim database acceptance. A scoped Python pass cannot mint this receipt.
Any required image build is recorded separately; a local test sweep is not a
successful deployment.

## Stop and rollback rules

Hold for a future/selectable missing day, unexplained tail, silent fallback,
cross-day substitution, truncation, request-time history scan, unsupported
geometry, seam/conservation failure, overlapping or missing owner, failed
recovery, stale source digest, incomplete service identity or missing rollback
pin. Return a failed contract to the implementation owner; do not patch runtime
behavior in the integration lane.

Before a deployment approval, pin the current compatible web/API/executor images
and schema/readiness contracts. A bare baseline revert is not a safe schema
rollback. Any proposed migration needs the retirement owner's exact preservation
set, rehearsal, ledger/pin compatibility and independently reviewed rollback.
Data corrections need original immutable inputs and terminal receipts, before/
after generation identities, publication barriers and supported compare-and-swap
or recovery commands; generic deletion is not rollback.

Scheduler rollback disables the affected executor lane and confirms work has
settled while preserving data, manifests and checkpoints. It never restores
Railway cron, reconnects an old writer or recreates a removed service. Production
acceptance's GREEN verdict is evidence for owner review, not permission to apply
any of these actions.
