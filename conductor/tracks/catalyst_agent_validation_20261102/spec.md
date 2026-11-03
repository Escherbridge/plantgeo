---
type: research-spec
---
# Lane B: Catalyst PUDL agent capability validation

Owner request: 2026-11-02. Separate from the data-fix deployment.

## Decision
Validate US-first PUDL data for existing agent workflows concerning water-loss prevention, wildfire prevention, outage prevention, and hydro/biomass generation. Produce evidence and acceptance criteria before enabling an energy ingestion lane.

## Required evidence
- Exact source tables, field meanings, grains, temporal coverage, version and access constraints.
- Fit against current agent tool schemas and selected-time/spatial evidence contracts.
- Explicit separation of retrospective facts, exposure screening, engineering estimates and unsupported prevention claims.
- Additional datasets/telemetry required for each unsupported claim.
- Pinned-release validation and bounded cost plan; no full ETL/import or paid inference as a side effect.
- Entity join and unit checks; unknown/allocated/imputed flags; missingness and historical-release semantics.
- Independent two-cycle dev-drive review shared with Lane A, using distinct review lenses.

## Acceptance
A capability matrix names supported questions, required inputs, missing evidence and abstention behavior. A concrete US pilot can admit plant inventory and generation facts without promising live grid state, causal interventions, water leaks, hydropower yield or biomass feedstock availability from PUDL alone.

## Release boundary
This lane has its own future source admission/release gate. It cannot block or silently expand the data-fix deployment. A validated integration design is not a shipped runtime tool.
