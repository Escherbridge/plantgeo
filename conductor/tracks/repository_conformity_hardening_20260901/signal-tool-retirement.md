---
type: track-evidence
status: implemented
---

# Generic signal tool retirement

The 2026-09-20 user request explicitly retires signal-layer references from the agent's MCP and
retrieval workflows. The four agent tools `signals_near_point`, `signal_value_on_day`,
`signal_neighbors_in_time`, and `nearest_signal_cells` read the generic `signal` partition prefix.
That prefix does not establish that a selected climate, soil, VPD, or vegetation product is
available. Product reads therefore supersede these tools rather than retaining aliases that
silently answer a different question.

The replacement is `surface_evidence_for_selection`, discovered through
`list_environmental_layers`. It reads the selected surface's declared serving lanes and carries
the selected day, active range, time scale, spatial tile, availability evidence, and continuation
bounds with its answer. The map surface catalogue owns the product-to-lane mapping. Climate and
soil surfaces now have individual region bindings instead of inheriting the generic signal binding.

The deletion boundary comprises the quartet's wrappers, query functions, exclusive scope/filter
helpers and constants, and their five DuckDB statements. The shared warehouse admission, column
probe, point/polygon SQL, availability checks, drought and fire implementations remain. Actual
metric columns named `signal_name`, frozen product schemas, published source artifacts, historical
migrations, ingestion code, and their tests are outside this retirement.

Static consumer review found the quartet through the single `WAREHOUSE_TOOLS` registry consumed
by MCP descriptors, the model tool catalogue, and the HTTP bridge; prompts and graph test fixtures
also named the old tools. Those discovery and prompt consumers are migrated with this change.
No HTTP route or deployed service is removed. Unknown old tool names produce the existing typed
unknown-tool response. There is no production request census establishing that external callers
never used these names: removing that public tool vocabulary is the requested compatibility
change, not a claim that external usage was measured as zero.

Tests whose sole subject was a retired query, generic cell universe, generic signal row cap, or
generic signal SQL parity are removed. Shared date propagation, spatial distance, read-only SQL,
availability/refusal, route, provider, and region-binding contracts remain. The real point-lane
DuckDB fixture now reads the dedicated VPD product. Selection-evidence tests cover the replacement,
and HTTP rejection cases preserve the retired-name boundary. The earlier assertions and SQL are
retained in Git history rather than kept as executable tests for an absent implementation.

Validation is recorded by the integrated end-of-batch checks for this change; this note does not
claim an independent full-suite pass. Rollback is a revert of the coordinated agent-tool change,
restoring registry, implementations, prompts, and region bindings together. No data restore or
database migration is required.
