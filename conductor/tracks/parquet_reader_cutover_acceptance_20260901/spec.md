---
type: track-spec
slug: parquet_reader_cutover_acceptance_20260901
status: planned
---

# Parquet reader hard cut and temporal acceptance

## Purpose

Make the production pixels, selectable days and terminal states come from one bounded Parquet
contract. This track takes the old pivot track's `d4` reader/capability scope; it does not own the
private API implementation, data construction, writer activation or PostgreSQL deletion.

Production browser assessment on 2026-09-01 found split ownership: the capability catalogue says
fire is Parquet-served, while `LayerManager` still uses `useFireData` -> `/api/fires` -> PostgreSQL.
The route is global, not bbox-bound, silently caps a day at 2,000 rows and returns raw points. The
existing `wildfire.getFireDetections` procedure already accepts day, bbox and zoom.

## Scope

- hard-cut fire first, then reconcile every eligible tRPC/capability reader;
- require settled day, bbox and zoom/rung for time-bearing map reads;
- preserve `published`, `governed_absence`, `day_not_written`, `lane_never_written` and
  `truncated` as distinct states;
- align `Latest` with the newest terminal day not beyond the source ceiling;
- make request cancellation reach the downstream reader;
- remove legacy routes only after measured parity and rollback evidence.
- replace cold coverage discovery with the checksum-bound lane availability artifact; after the
  one-time writer bootstrap, capability reads perform no historical object listing or data scan.

## Out of scope

- authoring missing Parquet days;
- designing filled polygons or isobands beyond freezing the response support contract;
- disabling a source writer, deleting PostgreSQL data or authoring retirement migrations;
- the final cross-product production acceptance verdict.

## Acceptance gates

1. No production fire pixel request reaches `/api/fires`.
2. Every fire request contains the selected settled day, current viewport bbox and zoom.
3. No unlabelled row cap exists; accepted responses assert `truncated=false`.
4. Panning changes bbox without changing day; scrubbing changes day without requesting a global
   collection; zoom breakpoints select exactly one physical rung.
5. Superseded requests stop server work as well as browser work.
6. Capability ceilings are source-specific and never future-relative; `Latest` is a terminal day.
7. Cold/warm catalogue time, day-row TTFB and request-to-paint are separately measured.
8. Rollback is an exact known deployment, never a hidden PostgreSQL fallback.
9. A cold slider capability request performs one availability-pointer GET and one availability
   Parquet GET per lane, with zero historical prefix listings and zero historical data-part reads.

The authoring verdict is independent from the final production verdict in
`parquet_production_acceptance_20260901`.
