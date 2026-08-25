---
type: track-spec
slug: regional_fire_risk_surface_20260824
status: chartered
---

# Cross-state fire-risk prioritisation surface

Chartered 2026-08-24 from the session recorded in `conductor/RUNBOOK.md` §0.41. Owner framing:
*"how can I shape this as a cross-cutting effort across the region"*.

## 1. Why this exists

Every institution working this landscape prioritises **inside its own boundary, by its own
method**: SageCon and OWEB in Oregon, WSRRI in Washington, the Sage Grouse Action Team in Idaho,
NRCS Working Lands for Wildlife federally, BLM by district. There is no common currency for
*where does the next dollar do the most good, regardless of which state it lands in*.

**Jurisdiction fragments; the warehouse does not.** The index in §0.41.2 is computed identically
across Oregon, Washington and Idaho because it reads one grid from one store. That is the
product: not another programme, but one surface any of those bodies can rank against.

Precedent that cross-boundary rangeland fire work is administratively possible: the BLM Tri-state
Fuel Breaks project already coordinates BLM Oregon, Idaho and Nevada with Owyhee County and Idaho
DOT across 3.6 M acres.

## 2. What it emits

A **day-partitioned Parquet lane**, per the ingestion constraint in RUNBOOK §0.41.7 — serving
reads one day for one layer; only the MCP asks for a historical list of days.

Grain: `(cell_id, valid_day)` → `risk_index`, plus the four standardised components so a consumer
can see *why* a cell scored, and the greenness stratum so a consumer can tell whether the score
is even in domain.

This is a **scalar per cell**, never a polygon — same rule `fire_risk_zone_forecast_20260823`
settled in RUNBOOK §0.28.5. Contouring is a rendering step.

## 3. What is already measured

From §0.41.2, leakage-free (features 1 Apr – 30 Jun; outcome opens 1 Jul), 736 rangeland cells,
268 burned:

| signal | AUC |
|---|---|
| composite index | **0.725** |
| vapour pressure deficit alone | 0.697 |
| soil temperature | 0.677 |
| surface soil moisture (inverted) | 0.605 |
| spring NDVI | 0.588 |

Decile lift 14.9 % → 67.1 % burned.

**Two honesty constraints this track inherits and must not quietly drop:**

1. **The composite beats VPD alone by ~0.03.** If a consumer only ever needs a screen, VPD is
   most of the value. Do not present the composite as a large improvement.
2. **It is one season, scored in-sample.** Until `fire_feature_plane_validation_20260824` clears,
   every number here is an association, not validated skill. The lane must carry that caveat in
   its own metadata, not only in this document.

## 4. Scope boundary

**In:** the lane, its schema, the single-day serving read, the MCP day-list path.

**Out:** model training (that is `fire_risk_zone_forecast_20260823`, blocked on the Mojo runtime);
carbon (that is `rangeland_carbon_lane_20260824`); validation (its own track).

**Explicitly out — forest.** VPD skill decays to 0.586 in the densest greenness quartile. The lane
must **refuse** out-of-stratum cells rather than extrapolate. A silently-wrong score on forest is
the most likely way this work does harm.

## 5. Open question for the owner

Does the surface serve **absolute** index values or **within-state percentile ranks**? Absolute is
the honest cross-boundary comparison and the whole point of the track; percentile is what each
agency's own prioritisation process will actually expect. Emitting both is cheap and probably
correct, but it doubles the contract surface.
