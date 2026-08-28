---
type: track-spec
slug: fire_feature_plane_validation_20260824
status: planned
---

# Validate the fire feature plane across multiple fire seasons

Chartered 2026-08-24 and **runnable as of 2026-08-27**. See `conductor/RUNBOOK.md` §0.41.9.

## 0. Unblocked checkpoint — 2026-08-27

The historical fire archive is exact across 9,428 calendar days: 8,359 data days, 1,069 governed
absences, and 3,039,749 detections. The old 2003-2025 data-availability blocker is therefore
cleared. Do not schedule another fire backfill or resume its completed data task.

The modeling work in this track has **not** run. It remains planned: held-out-season validation,
calibration, coverage weighting, and MODIS/VIIRS normalization still need implementation and review.

## 1. The problem in one line

`AUC 0.725` was fit **and** scored on the 2026 season. That is in-sample and optimistic, and no
treatment budget should be committed against it until it survives a season it has never seen.

## 2. Historical blocker — cleared

At charter time, `fire-detections` in the warehouse held:

| year | days present |
|---|---|
| 2000 | 35 (Nov–Dec only) |
| 2001 | 233 |
| 2002 | 270 |
| 2003 | 1 |
| **2004–2025** | **none** |
| 2026 | 224 (to 22 Aug) |

A **23-year hole** existed in that historical census. An empty year meant *not yet backfilled*,
never *no fire*.

That blocker was cleared by the completed fire replacement and exact production audit. The table is
retained only as the reason this modeling track originally waited; it is not current inventory.

## 3. Remaining validation work

1. **Out-of-season validation.** Fit on one year set, score on a held-out year. Report the drop
   from in-sample to out-of-sample honestly — that gap is the actual deliverable, more than the
   headline number.
2. **Calibration.** The index is currently an unbounded z-score composite. A prioritisation
   surface needs a *probability* with a reliability curve, or "carbon at risk = stock ×
   probability" has no defensible second term.
3. **Re-test the two refuted claims** (RUNBOOK §0.41.4) against the fuller record. Both were
   rejected on 2026 alone; more seasons could revive or bury them, and the honest move is to look
   rather than assume the single-season verdict generalises.

## 4. Traps specific to multi-year work

- **Detection coverage is wildly uneven across years.** 2000 has 35 days, 2003 has one. An
  unweighted multi-year fit silently weights 2002 about eight times 2000. Weight by coverage or
  restrict to years above a day-count floor, and say which.
- **The upstream feed caps records per request and drops the excess silently** — worst on exactly
  the large fire days that dominate the outcome.
- **Ignition is not modelled.** A year with an unusual lightning outbreak will read as model
  failure when it is really an unmodelled driver. Expect it; do not tune it away.
- **The sensor changed.** Detections spanning 2000–2026 cross MODIS-era and VIIRS-era platforms
  with different footprints and detection limits. A raw count is not comparable across that
  boundary without normalisation, and this is the single most likely way to manufacture a fake
  trend.
