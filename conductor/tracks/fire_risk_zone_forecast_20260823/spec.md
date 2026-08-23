---
type: track-spec
slug: fire_risk_zone_forecast_20260823
status: chartered
---

# Fire-risk zone forecast — 1–2 weeks ahead

Chartered 2026-08-23. Owner: *"we have historical fire detections, historical burn data, vegetation
data, water moisture data with the seasonality trends it should be possible to provide a best guess
region for 1-2 weeks ahead ... having it get a self correcting ml model may be best."*

## 1. What this predicts, and in what shape

**Not a polygon.** RUNBOOK §0.28.5 settles this: forecasting shape evolution is a different and much
harder problem, and nothing here needs it. This predicts a **scalar risk score per analysis cell per
day**, 1–14 days ahead. Turning cell scores into a displayable region is a **contouring/rendering
step**, not a forecast step.

Grain: `(cell_id, valid_day)` → `risk_score`, plus the calibrated probability it derives from.
This keys into the same cell × time × metric contract every other time-series lane uses.

## 2. Feature plane — BUILDABLE NOW

Model training is blocked (§4), but the covariate plane is not, and it is the long pole. One row per
`(cell_id, observed_day)` assembled from streams that already export to Parquet:

| family | source stream | notes |
|---|---|---|
| ignition history | `fire-detections` | already a 0.005° cell-day aggregate — count, summed FRP, high-confidence count |
| fuel state | `vegetation` | NDVI; the deepest record at ~4 years |
| moisture | `signal` | soil wetness (NASA, degree of saturation) and soil water content (ERA5-Land, m³/m³) — **different physical quantities, never blend** |
| weather drivers | `signal` | temperature, relative humidity, wind speed, VPD, precipitation |
| burn history | `burn-severity` | prior-year burn footprint suppresses near-term reburn (fuel consumed) |
| drought | `drought` | weekly USDM class, cell-reduced |
| **seasonality** | `dim_date` | **owner requirement: seasonality must influence output — fires are heavily seasonal.** Cyclical sin/cos day-of-year, not raw ordinal |
| photoperiod | solar fact table | daylight seconds per `(cell_id, date)` — see RUNBOOK §0.28.3 |

**Leakage is the failure mode that will invalidate this silently.** Every feature must be computed
from data available at `issued_on`, respecting each producer's publication lag (NASA POWER 5 days,
ERA5-Land 9, radiation ~60). A feature built from a day the model could not have seen produces
excellent backtests and a useless product.

## 3. Labels and evaluation

Label: did ≥1 fire detection occur in the cell within the horizon window. Severely imbalanced —
most cell-days have no fire — so accuracy is meaningless. Score with **precision/recall at operating
thresholds, PR-AUC, and Brier/reliability** for calibration.

**It must beat two baselines or it does not ship**: (a) climatology (this cell's historical
seasonal ignition rate), and (b) persistence. RUNBOOK §0.28.4 makes this binding for every forecast.

"Self-correcting" means the loop is closed: each issued forecast is scored against what actually
burned, and that score feeds recalibration. `method/ml/conformal_calibration.py` already exists.

## 4. What is BLOCKED and why

**No Python model gets trained.** Owner call: ML is frozen pending the Mojo runtime migration, and
fire-risk is chartered "blocked on the Mojo decision" so no model is written then thrown away.
Depends on [`ml_mojo_conversion_20260823`](../ml_mojo_conversion_20260823/spec.md).

The **feature plane, the label plane and the evaluation harness are NOT blocked** — they are data
work, they are the long pole, and they are portable to whatever runtime wins.

## 5. Known traps carried in

- **FIRMS silently drops beyond 10,000 records** and "0 written" is by-design idempotency, not
  success — a measured 2,239-record loss hid this way. Training on a silently-capped label set
  teaches the model that fires stopped happening.
- **VIIRS and MODIS FRP differ by roughly an order of magnitude** for the same physical fire and are
  not currently split by instrument.
- **`burn-severity`'s legend ramp is keyed on acres, not MTBS severity classes**, and
  `severity_class` is 100% null today.
- Fire detections are **observations of detection**, not of fire — cloud cover and satellite overpass
  timing modulate them. Absence of detection is not absence of fire.
