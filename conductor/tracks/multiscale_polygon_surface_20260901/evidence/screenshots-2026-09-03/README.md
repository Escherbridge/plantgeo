---
type: track-evidence
track: multiscale_polygon_surface_20260901
slice: m5
status: partial_production_captures
observed_at: 2026-09-03
---

# Production captures — 2026-09-03, web `1da1a28`, Parquet API deployment `3a3430bf`

Headless Chromium (Playwright 1.62.1, `--use-gl=angle`, SwiftShader), anonymous context per scenario,
1440×900, JPEG quality 70. Camera via `?focusLng&focusLat&focusZoom`; layers toggled through the Map
manager rows. The raw run report (requests, timings, response heads, row text) is
`browser-check-report.json`. Full procedure and verdicts: gapless track evidence
`post-deploy-tick-2026-09-03.md`, "Step 2".

| capture | camera | layer | reader answer | what is drawn |
|---|---|---|---|---|
| `fire-default-pnw.jpg` | default PNW (~z6) | Fire Detections, 2026-09-01 | `wildfire.getFireDetections` `ready`, 0.2° `aggregate_cell` support, `aggregationMethod: count` | density cells, no perimeters |
| `climate-air-temp-z8.jpg` | −120.5, 45.5, z8 | Air temperature, 2026-08-06 | `environmental.getClimateField` FeatureCollection of 1° polygons, `aggregated: true`, `coverageFraction: 1` | filled tessellation, one rung; cell lines are the `fill-outline-color` stroke |
| `vegetation-water-z5.jpg` | −118, 45.5, z5 | Vegetation (NDVI) 2026-09-01 + Water Gauges 2026-09-03 | `getVegetationIndex`, `getStreamflow`, `getGroundwater` | vegetation 0.25° cells; water gauges as cells |
| `soil-moisture-z5.jpg` | −118, 45.5, z5 | Soil Moisture (ERA5-Land), 2026-08-02 | `environmental.getSoilField` 474 KB of 0.25° polygons for `soil-field-moisture-0-7cm` | see the gapless evidence for the verdict |

Still owed by m5: pixel-level seam/crack checks, the historical-day and governed-empty-day captures,
the middle/detail zooms per layer, and the hover caption capture for fire (the not-a-perimeter line
lives in the hover tooltip and automation did not surface it).
