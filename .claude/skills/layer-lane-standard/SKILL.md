---
name: layer-lane-standard
description: >
  The end-to-end contract every PlantGeo data layer must satisfy: a declared
  history horizon, a forward refresh, automatic gap detection that turns holes
  into work items, governed absences for days upstream cannot serve, a Railway
  cron for each of those three, a serving reader registered in the slider
  capability catalogue, and agent tools that answer at the UI-selected time plus
  temporal and spatial neighbours. Use when adding a layer, wiring a producer,
  giving a layer history or a time slider, closing coverage gaps, exposing a
  layer to the agent, or auditing whether a layer is actually finished.
---

# Layer Lane Standard

**The canonical document is [`docs/layer-lane-standard.md`](../../../docs/layer-lane-standard.md). Read it now — it is the governing contract, not a summary.**

It lives under `docs/` rather than here on purpose: `.claude/` is a tooling
directory that is gitignored in many checkouts, and this standard must survive
for humans and for tools that never load a skill. This file is a pointer so the
two cannot drift — do not copy the contract back into it.

## What it covers

1. First principle — a lane that reports success having written nothing is worse
   than a lane that fails
2. Declaring the lane, once, with `source_key`/`grid_name` verified against the DB
3. The two-level history horizon (static floor + live availability, unioned over
   the whole span)
4. Which of the four write planes the layer lands in
5. `observed_at` vs `data_available_at` — the leakage boundary
6. Cadence-aware gap detection, one engine
7. Turning a gap into a work item — the loop that closes it
8. Governed absences for days upstream cannot serve
9. Three crons per lane family, and the Railway traps
10. Serving, and registering the stream in the slider capability catalogue
11. Time-slider wiring
12. Agent tools — selected-day, temporal proximity, spatial proximity, all
    carrying their distances
13. House style
14. Definition of done, as a checklist
15. Verified environment facts (ports, DSNs, grid names, the silently-skipping
    contract suite)

## When to load it

Adding a layer; wiring a producer; giving a layer history or a time slider;
closing coverage gaps; exposing a layer to the agent; or auditing whether a layer
is genuinely finished rather than merely rendering.

Related: the `agri-pipelines` skill covers running and debugging the existing
pipelines; this one covers the contract they must all satisfy.
