---
type: track-spec
slug: parquet_production_acceptance_20260901
status: blocked
---

# Production Parquet temporal and spatial acceptance

## Purpose

Provide one independent, evidence-only fan-in verdict for the reader, publication and spatial tracks.
This track does not author fixes. Failed evidence returns to its owning track, and a new immutable
candidate must be supplied before the relevant check reruns.

## Matrix

Every production product is checked across:

- newest eligible terminal day, a populated historical day and a governed-empty day;
- coarse, middle and detail zooms at the default Pacific Northwest camera;
- cold and warm catalogue/read paths;
- coverage, day, window and release/refusal routes as applicable;
- temporal state, response bounds, rung, support geometry and painted result.

## Evidence required

- exact Git commit and Railway service/deployment matrix;
- private R2 route status, manifests, receipts, bytes, rows, bbox bounds and truncation;
- availability pointer/generation SHA, schema, authoritative required-rung set, immutable bootstrap
  receipt key/SHA, source inventory root, terminal/source receipts, absence reasons and trace proof
  that capability reads issue no historical LIST or data-part reads;
- catalogue time, day-row TTFB, request-to-paint, requested-versus-painted day and cache status;
- screenshot/canvas-pixel continuity plus rung conservation;
- at least three scheduled advances and one recovery exercise for each activated product owner;
- one final integrated lint/typecheck/test sweep and independent review;
- exact rollback revision and unresolved risk list.

## Stop conditions

Any future capability ceiling, unexplained tail, selectable missing day, silent fallback, truncation,
cross-day substitution, request-time historical coverage scan, overlapping writer, missing owner,
failed recovery, geometry seam, fake perimeter, simultaneous rung or conservation failure keeps the
verdict RED.

## Authority boundary

A GREEN verdict may hand evidence to `postgres_shrink_ingest_repoint_20260825` for its P5/P6
retirement review. It does not disable a writer, delete PostgreSQL/R2 data, run a retirement
migration or authorize an unreviewed production mutation.
