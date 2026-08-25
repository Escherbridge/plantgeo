---
type: track-spec
slug: soil_survey_lane_shape_20260825
status: pending
---

# Design the partition grain for a static Parquet lane

Chartered 2026-08-25, **pending / not started**. Deliberately deferred from the 2026-08-25 data-completeness session by owner decision. See `conductor/RUNBOOK.md` §0.31.1 (the correction) and §0.29.1 (the wrong version it corrects).

## 1. What the problem looks like

Measured 2026-08-25 by `agri-cli parquet-drain --dry-run --selection ladder`:

```json
{
  "lane": "soil-survey",
  "base_days": 0,
  "base_absent_days": 0,
  "incomplete_ladder_days": 0,
  "ladder_complete_days": 0
}
```

Every other Parquet lane has data. This one has none at all.

## 2. Why the recorded cause is wrong

`conductor/RUNBOOK.md` section 0.29.1 states: "`soil-survey` writing NOTHING is CORRECT: its Postgres source is filled only by historical backfill, which is complete, so the lane is done."

**This explanation is false on both counts.** §0.31.1 corrects it:

- **The state is wrong:** 238,986 rows exist in the Postgres source (`geo.soil_survey`). It is not empty.
- **The reason is wrong:** The lane never drains on its own because the export hits a cap of 200,000 keys and soil-survey has 200,001 keys. `MAX_SOIL_SURVEY_POLYGON_KEYS: Final = 200_000` in `lane_registry.py:96` causes the export to **raise** rather than return a truncated result. A lane that raises looks identical to a lane with no work, which is why this went unnoticed.

Contrast: `burn-severity`'s zero IS correct and must not be "fixed" (RUNBOOK §0.29.5, decision 5).

## 3. The owner's framing: not a quick fix, but a design question

Owner call 2026-08-25: *"soil survey is static but large so it may need a different shape."*

Soil-survey is not a time series like the other lanes — it is a static, large delineation dataset (~1.5M delineations per RUNBOOK section 0.32.2 decision 4, which is what `MAX_DERIVATION_ROWS = 5_000_000` in `warehouse/parquet/tiers.py` was sized to admit). Day-partitioning a dataset that does not change by day may be the wrong shape entirely.

**This track must NOT assume "raise the cap and drain it" is the answer.** That is one candidate among several. The track exists to answer a design question, not to apply a one-line fix.

## 4. Open questions this track must record

These are the work to be done. Do not answer them; record them and make them concrete for whoever picks up the track next.

### 4.1 Is day-partitioning the right shape at all?

Should soil-survey be a **single sealed partition with no day dimension**, like a static lookup table? Or is there a reason to keep the `year=YYYY/month=MM/day=DD/zoom=Z` grain?

**Context:** Other lanes (vegetation, drought, etc.) are rolling time series that append new days forever. Soil-survey is a snapshot: SSURGO map units do not change by day in any meaningful way.

### 4.2 What does a 200,001-key export cost?

If the cap is simply raised or the export is paginated, what does a 200,001-key write cost in memory and time? Does it fit the guards in `warehouse/parquet/tiers.py` (1600MB / 3 threads / spilling disabled)?

### 4.3 What does the zoom ladder mean for a static lane?

Coarse rungs (z0, z5, z9) are derived from base z13 by aggregation. For a static dataset, do coarse rungs get derived **once**, or **not at all**? If derived once, they are frozen; if not at all, the serving API has no coarse geometry.

### 4.4 Which serving reader and slider capability entry does soil-survey need?

What does the serving reader (`interface/http` per RUNBOOK 0.33.3 item C) expect to find in Parquet for a static lane? Does the existing **`_static_lane_census`** bracketing in `gap_fill.py` already model this correctly, or does soil-survey need a different shape?

**Context:** §0.41.7 and `foundation/parquet/lane_contract.py` define `nature` declarations per lane (e.g., `daily_series`, `static_lookup`). Soil-survey's eventual nature must align with what the serving API and slider expect to consume.

## 5. Status

**Blocked on owner design decision** (what shape soil-survey should take). Once that decision is made, implementation can follow a straightforward path: raise the cap and drain, or reshard, or restructure to a static partition — the shape determines the work.

**Out of scope for 2026-08-25 data-completeness session** by deliberate owner choice (RUNBOOK §0.29.1 context). Deferred here so it is not lost.
