---
type: track-evidence
slug: gapless-parquet-p0a-offline-verification
status: code_complete_offline_verified
---

# P0A offline implementation verification

## Current disposition

**CODE COMPLETE — OFFLINE VERIFIED.** The final authoring pass closes the remaining HIGH race with a
lane-wide PostgreSQL shared/exclusive publication barrier. Writers take the shared lane barrier before
their existing exclusive day lock; availability takes it exclusively from before receipt verification
through every conditional pointer attempt and rebase. The root-coordinated integrated gate is green,
and separate read-only review approved the combined tree with no findings.

This disposition does not authorize or claim a production bootstrap, R2 mutation, scheduler
activation, PostgreSQL change, deployment or HTTP/tRPC/TypeScript reader cutover.

## Scope and outcome

The implementation covers the canonical availability schema, immutable generation
publisher, one-time bootstrap, fail-closed reader, conditional object-store adapter and
offline-first CLI commands. The release amendment adds canonical typed receipt contracts and exact
semantic/object cross-binding, retained evidence identities with immediate pre-CAS revalidation,
and purpose-specific bounded reads with pre-download pointer limits.

The implementation started from commit `88dff29535339c08f97a55bf258417674268cd92` on branch
`codex/gapless-parquet-availability-p0`.

The rejected predecessor was commit `3e6a7089fc14f49076475aec0aad28c5bb833846`; it is not the
terminal implementation SHA. The exact replacement implementation commit is recorded in the final
commit reconciliation below.

## Independent release review

A separate read-only reviewer inspected the full amended implementation across correctness,
security, performance, maintainability and test adequacy. The review first returned
`NOT APPROVED` and identified these merge blockers:

- opaque/SHA-only receipts did not prove their lane, product, day, rung, count, terminal state,
  source ceiling or exact source/data/completion object bindings;
- overwriteable evidence could change between initial verification and pointer CAS;
- Boto reads and pointer-declared generation sizes were not purpose-bounded;
- authoritative completion/absence wire bytes, 409-versus-412 immutable-write behavior and focused
  proof cases needed correction.

The authoring pass closed the receipt and bounded-read findings and added adversarial proof for
semantic swaps and mismatches, same-byte/new-identity data and completion mutation, oversized
pointer/generation/evidence bodies, pointer pre-GET limits, Parquet pre-materialization limits,
system-bootstrap cross-binding, 12-part numeric ordering and large physical observation counts. A
later reviewer found the final verification-to-CAS ownership race; the lane-wide barrier repair and
its adversarial tests closed that race.

The fresh final review returned **APPROVED with no findings** for typed receipts, bounded reads,
publication/CAS/rebase ownership, lock ordering, serving-writer callsites, CLI behavior and pinned
session lifetime. Its reviewed-diff fingerprint was
`6b9e75e419d10b29d3c874019d93d2cc649eb25f`. The integrated gate then exposed four mechanical
authoring defects: a nested-context lint finding, a multi-statement `pytest.raises` lint finding and
two local-class annotations, one of which caused the only test failure. Those exact corrections were
separately reviewed read-only and received **APPROVED with no findings** before the final green gate.

## Final integrated Python gate

Command, from `services/agri-data-service`:

```text
uv run --extra dev python scripts/check.py
```

The final run after all implementation and review repairs passed every repository-defined stage:

| stage | result | duration |
|---|---:|---:|
| format check | PASS | 0.12s |
| Ruff lint | PASS | 0.13s |
| Mypy | PASS | 3.20s |
| full Pytest suite | PASS | 126.06s |

For audit completeness, the first root-coordinated pass after the barrier repair reported format and
Mypy green, four Ruff findings and one failing test caused by an unquoted local-class annotation; the
remainder was 4,525 passing and 140 skipped. Only those four mechanical defects were changed. The
corrected integrated command above then passed in full; the resulting suite accounts for 4,526
passing tests and the same 140 environment-gated skips. No production database or object-store test
target was enabled by this offline session.

## First authorized apply prerequisite

No apply was run. Before the first separately authorized availability bootstrap or publication,
operators must drain or restart every writer process deployed before the lane-publication barrier.
Such a process cannot participate in the new shared/exclusive protocol merely because newer code has
landed. Only after old writers are gone may the guarded availability command acquire the exclusive
lane barrier and perform receipt verification through pointer CAS. This is a deployment prerequisite,
not authorization to mutate R2, PostgreSQL, Railway, schedules or reader state.

## Contract proof covered

- Exact Arrow field order, physical types and nullability, including a non-null list of non-null
  data-receipt structs and canonical ordered rungs `(0, 5, 9, 13)`.
- Deterministic bootstrap inventory-root derivation, exact input SHA validation, immutable receipt
  binding and idempotent replay after successors without reopening historical inputs.
- Canonical duplicate-safe bootstrap, source and terminal receipt documents with exact field sets,
  content-addressed typed paths and row-for-row lane/product/day/rung/state/count/source-ceiling/time
  and source/data/completion/absence cross-binding.
- Complete same-state rung ladders, receipt-bound source ceilings, governed absences and strict
  refusal of empty, partial, mixed, stale, malformed, duplicate-key or checksum-invalid evidence.
- Immutable generation write and reread before the last conditional pointer update, with exact
  pointer/Parquet metadata cross-binding and distinct semantic versus physical byte receipts.
- Idempotent replay, safe disjoint race rebase, strictly newer same-grain correction, retained
  generation rollback with target evidence revalidation, and pointer stability on refusal.
- S3/R2 `If-None-Match` creation, `If-Match` pointer advancement, bounded 409 retry and exact-only
  412 adoption.
- Purpose-specific pointer, typed-receipt, bootstrap, generation and evidence byte ceilings;
  `ContentLength` preflight, bounded body reads and close behavior; pointer byte/row refusal before
  generation GET; and Parquet row-metadata refusal before table materialization.
- Evidence snapshots retaining SHA-256, byte count, ETag and optional VersionId, revalidated after
  immutable generation publication and immediately before every pointer CAS attempt.
- Offline/no-network defaults for both bootstrap and publication commands.
- Shared writer/exclusive availability lane barriers with stable keys, fail-fast contention, exact
  lock ordering and a source-loader-session-only `--apply` path. Adversarial tests cover writer versus
  availability exclusion, concurrent days, independent lanes, no object I/O under contention, the
  CAS hook window and retained exclusive ownership across a conflict/rebase.

## Remaining work outside P0A

P0 remains incomplete under `evidence/product-ownership-census.md`: fresh production ladder census,
provider receipts, source floors/ceilings/lags/cadences and no-overlap handoffs remain unmeasured or
unauthorized. Production bootstrap and reader slice `r2` are separate follow-up sessions.
