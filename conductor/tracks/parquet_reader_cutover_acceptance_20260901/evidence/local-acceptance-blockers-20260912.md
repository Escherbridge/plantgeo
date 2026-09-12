---
type: track-evidence
slug: parquet_reader_cutover_acceptance_20260901
artifact: local-acceptance-blockers
date: 2026-09-12
status: blocked_on_runtime_evidence
source_commit: 6c08907ba392d8dcdff3e96c41fdb27c6a74fe9d
source_tree: 92f3baef253389b737f3e36fd47a96b52c43c312
---

# Local reader-acceptance blocker receipt

This continuation re-read the current plan, the September 12 availability and
independent-review receipts, the current reader/capability contracts and the root
task ledger. The inspected reader and capability source paths have not changed
since the receipt base `8c14ea0117ba14d5584f737b793f989566102514`;
the intervening changes in this track are the already-reviewed Conductor
reconciliation. No Railway, production object storage, PostgreSQL, `pgt`, writer,
scheduler, publication pointer or data load was accessed or controlled.

## Requested day and served day

The contract has two dates because they answer different questions:

- `requestedDay` is the slider/as-of day the caller asked about.
- `servedDay` is the published day or snapshot that actually answered. A ready or
  governed-absence envelope carries both; a `day_not_written` or
  `lane_never_written` envelope carries only the requested day
  (`src/lib/server/services/parquet-trpc-readers.ts:1000-1028`).
- For the `static_lookup` fire-perimeter lane, release resolution chooses the
  newest snapshot at or before the request. The in-frame temporal predicate then
  uses the requested day, not the older snapshot day
  (`parquet-trpc-readers.ts:2040-2090`; `src/lib/map/AGENTS.md:312-325`).
- The map binds its visible requested date to the settled slider day and derives
  the drawn answer from the query result (`src/components/map/LayerManager.tsx:63-69`,
  `:753-761`). Placeholder retention is therefore a loading-state fact, not
  permission to relabel the retained `servedDay` as the answer for the new day.

These semantics are tree-provable. This audit did not issue a browser request, so
it does not prove that a deployed pan/scrub/zoom trace preserves them.

## Provenance and source-ceiling caption remains open

`getParquetSliderCapabilities` still emits `coverageAuthority` and
`sourceCeilingDay` (`src/lib/server/services/parquet-slider-capabilities.ts:734-745`).
The public row type still declares both (`src/types/time-slider.ts:247-261`), but
no component under `src/components/map/layer-panel` consumes either field. The
slider instead composes its visible coverage note without them. Consequently:

- an availability-index answer and a census/object-walk answer remain visually
  indistinguishable; and
- a lane correctly bounded by its upstream source ceiling remains visually
  indistinguishable from a lane whose publication is merely behind.

The existing R2 caption checkbox stays open. The type comment describing
`sourceCeilingDay` as an absolute axis limit also remains narrower than the
implemented recorded-versus-carried-day rule documented in
`local-availability-contract-20260912.md`; this receipt records the mismatch but
does not edit source documentation under the evidence-only boundary.

## Cold/warm and request-trace blocker

The source identifies possible request classes, not observed production traffic:
a full uncached lane read can use a pointer and generation GET; a matching rollup
can avoid the generation GET; a stale/mismatched rollup can add a probe and
fallback; a missing pointer can add a bootstrap-marker GET; census-policy lanes
can still invoke a listing. Inner and outer caches have distinct lifetimes. The
literal gate-9 pair of GETs therefore cannot be inferred from static call sites.

The missing runtime packet must record, for coarse, middle and detail zoom:

1. cold and warm catalogue time;
2. day-row TTFB;
3. request-to-paint time;
4. cache state and policy used for each run;
5. per-lane pointer, generation, rollup and bootstrap-marker GET counts; and
6. zero historical prefix LIST and zero historical data-part reads.

It must also retain the deployed commit/configuration identity and the browser
pan/scrub/breakpoint trace. Cancellation remains tree-proven with the batching
asymmetry recorded in `reader-cutover-verdict.md`; this continuation does not
reopen or reassign gate 5. No item above was measured in this local-only
continuation. Gates 7 and 9, plus the production halves of gates 1 and 4, remain
owned by `parquet_production_acceptance_20260901`.

## Verdict

No reader acceptance checkbox can close from this continuation. The current tree
continues to support the existing local contract receipt, while the caption and
runtime packets above remain blockers. Historical timing comments and historical
passing suites are not promoted to measurements or validation of this candidate.
