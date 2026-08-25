---
type: track-spec
slug: rangeland_carbon_lane_20260824
status: chartered
---

# Rangeland soil-carbon lane — SoilGrids SOC / OCD

Chartered 2026-08-24. See `conductor/RUNBOOK.md` §0.41.5.

## 1. Start with what this is NOT for

RUNBOOK §0.41.4 is unambiguous: **no carbon-targeting signal was found in the fire-risk work.**
The index predicts where fire *occurs*, not how much carbon a fire *releases*. Two candidate
carbon stories were built and then refuted by their own evidence — an intensity inversion that
was a `frp_sum` duration artefact (correlation +0.131 once normalised per detection), and a
"large fires happen in low-risk cells" gradient that died on medians (16 / 84 / 13 against means
of 1256 / 957 / 398).

**This lane must therefore not be justified as fire targeting.** It stands on its own: the estate
has a carbon variable it has never sampled.

## 2. Source

`geo.published_raster` already catalogues ISRIC SoilGrids v2.0 over −127.04→−110.17, 42.00→49.00,
as both COG and PMTiles. Two products matter:

| product | unit | scale_divisor | range | object key |
|---|---|---|---|---|
| `soc_0-5cm_mean` | g/kg | 10 | 5.7 – 462.1 | `raster/soil/soilgrids-v2.0/soc_0-5cm_mean_4326.tif` |
| `ocd_0-5cm_mean` | kg/m³ | 10 | 10.3 – 111.2 | `raster/soil/soilgrids-v2.0/ocd_0-5cm_mean_4326.tif` |

`ocd` (organic carbon **density**, kg/m³) is the better stock variable; `soc` (concentration,
g/kg) needs bulk density to become a stock, and `bdod_0-5cm_mean` is published alongside it.

**`geo.soil_survey` has no carbon field** — it is SSURGO map units (drainage class, land
capability, soil series). SoilGrids is the only carbon source in the estate.

## 3. Lane nature — the decision that shapes everything

**`static_lookup`, keyed to a source watermark.** Per `foundation/parquet/lane_contract.py` and
the owner constraint in RUNBOOK §0.41.7, every lane declares what its partition day *means*. SOC
is a modelled climatology; it does not vary by day. A `daily_series` nature here would write
identical values 365 times a year for no information gain.

The lane is otherwise idle and re-exports only when ISRIC publishes a new version.

## 4. Grain

`(cell_id, product, depth)` → value, with `scale_divisor` **already applied** so no consumer can
forget it, plus the sampling method and the source release for provenance.

Sampled at the same `sentinel2-ndvi-0p25deg` cell centroids the signal and vegetation planes use,
so it joins with no regridding.

**Open question:** centroid sample or cell-mean? A 0.25° cell is ~28 km and SoilGrids is 250 m, so
a centroid is one pixel standing for ~12,000. Cell-mean is more honest and materially more
expensive. Recommend cell-mean with the pixel count retained as a quality column.

## 5. Environment

`rasterio>=1.3,<2` is pinned in `pyproject.toml` and was installed with **`uv pip install`, not
`uv sync`** — this venv is known to drop `pytest` on sync. Pinning it in `dependencies` is what
makes a future correct sync keep it.

Read COGs **windowed**. A full-array load is the same class of mistake as the cross-join in
RUNBOOK §0.41.6 that consumed the host.

## 6. What it unlocks

Carbon-at-risk per cell: stock × burn probability, once
`regional_fire_risk_surface_20260824` emits a calibrated probability rather than a raw index.
That is the first defensible carbon number this project could publish — and it is a *product* of
two lanes, not a claim either can make alone.

For scale, from the literature and **not** measured here: cheatgrass conversion costs 6–9 Mg C/ha
belowground, roughly double the aboveground loss, appearing below 20 cm and more than five years
after fire. Any figure this lane emits should be checked against that order of magnitude.
