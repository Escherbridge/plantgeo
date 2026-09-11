---
type: track-plan
slug: parquet_duckdb_pivot_20260823
status: active
resource: ./spec.md
---

# Current umbrella handoff plan

The [specification](spec.md) preserves the historical charter. This plan schedules
only residual umbrella work against the September 11 authority and the
[integration ledger](evidence/integration-ledger-20260911.md). No historical drain,
cron, migration or data-construction instruction is restarted.

- [x] Preserve the completed d0/d1/d3/d5 slices and their exact historical receipt
  boundaries through the [cutover archive](../../retros/parquet_cutover_completed_slices_20260910/README.md)
  and [operational retrospective](../../retros/parquet_operational_checkpoints_20260911/README.md).
- [ ] Accept the reader's exact no-fallback product/day/zoom/cold-warm handoff,
  including selected-day agent behavior and honest coverage/source-ceiling state.
- [ ] Accept direct-writer, history/gap/absence, ownership and recovery handoffs
  from gapless and product repair owners without treating preparation as publication.
- [ ] Reconcile remaining static/product scope explicitly: static SoilGrids and
  soil-survey restoration with retirement, watershed provisioning/coverage ownership,
  upstream Open-Meteo expansion and the fire-risk feature successors. A named
  successor is a scope handoff, not evidence that its product shipped.
- [x] Inventory older soil-wetness, precipitation, dew-point, burn-severity and
  drought requests against their actual historical horizons in the
  [retained-task reconciliation](evidence/historical-horizons-20260911.md).
  Physical history, final parity and current acceptance remain distinct; all
  named gaps and missing receipts remain open with their successor owners.
- [ ] Accept the independent production acceptance verdict on the exact deployed
  tree and required products/states/rungs. Record publication and scheduled
  advancement evidence separately from code completion.
- [ ] Reconcile registry, metadata and runbook together only after accepted
  evidence supports the umbrella's residual product and handoff criteria.

The umbrella remains active during integration. A blocked external gate is named
in the ledger; it is not silently completed by the acceptance of local commits.
