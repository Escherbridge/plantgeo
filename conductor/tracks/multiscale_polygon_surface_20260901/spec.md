---
type: track-spec
slug: multiscale_polygon_surface_20260901
status: planned
---

# Multiscale polygon and continuous-surface rendering

## Purpose

Make every zoom rung visually continuous and semantically honest. The 2026-09-01 production browser
assessment showed raw fire dots at coarse zoom, separated rectangular climate blocks and nested ERA5
soil blocks with visible seams. MTBS rendered coherent source polygons and is the native-geometry
reference.

## Render classes

| class | products | accepted coarse/middle form | accepted detail form |
|---|---|---|---|
| `continuous_field` | climate, soil moisture/temperature, VPD, wetness, precipitation, radiation, wind, humidity | complete tessellating cells, filled dissolved isobands or governed raster surface | fine support cells or field surface |
| `event_point` | fire detections, gauges, stations, sensors | declared aggregate-cell polygons, heatmap or clusters | raw source points |
| `native_polygon` | MTBS, perimeters, drought, evacuation, watersheds, SSURGO | topology-preserving simplify/dissolve | source geometry |
| `reference_or_unavailable` | products without a valid spatial form | explicit unavailable/reference state | declared source form only |

Fire polygons in this track are **satellite detection-density cells**, never physical fire
perimeters. Vegetation preserves its actual 0.25-degree support rather than inferring a fictitious
finer footprint. Genuine stations remain points at detail zoom.

## Serving contract

Every aggregate envelope declares `zoomTier`, `supportKind`, stable support ID, origin-versus-center
semantics, cell width/height or native geometry, aggregation method, contributor count and
provenance. The client does not infer support from a null cell ID or layer name.

## Acceptance gates

- One physical rung renders at a time.
- Neighboring cells share bit-identical boundaries and no map background appears through cracks.
- Source-part and batch boundaries do not appear as nested blocks.
- Aggregate counts/sums conserve the detail rung within the declared aggregation semantics.
- Continuous fields fill polygons/surfaces rather than drawing contour strokes only.
- Event aggregates are visually and textually distinct from native perimeters.
- Feature count, bytes and request-to-paint remain bounded at the default PNW camera.
- Screenshot and canvas-pixel tests cover coarse, middle and detail zoom transitions.

## Out of scope

This track does not fill missing temporal days, activate writers, change source ceilings, fall back
to PostgreSQL or authorize a release. Those gates belong to the publication and acceptance tracks.
