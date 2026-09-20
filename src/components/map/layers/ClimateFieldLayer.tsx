"use client";

import { useCallback, useEffect, useMemo, useRef } from "react";
import type { Map as MapLibreMap, GeoJSONSource } from "maplibre-gl";
import { getFirstSymbolLayer, safeRemoveLayerAndSource } from "@/lib/map/layer-utils";
import {
  climateFieldColorStops,
  climateFieldSignalDefinition,
  CLIMATE_FIELD_ATTRIBUTION,
  type ClimateFieldSignalId,
  type ClimateRenderForm,
} from "@/lib/environmental/climate-field";
import { scaleOpacityValue } from "@/lib/map/layer-opacity";
import { BASE_ZOOM_TIER, type ZoomTier } from "@/lib/map/zoom-tiers";
import type { ExpressionSpecification } from "@/types/map";
import { measuredValueLabelLayer } from "@/lib/map/measured-value-label";
import { climateFieldLayerIdsFor } from "@/lib/map/climate-field-layer-ids";

/**
 * One NASA POWER signal, drawn from whatever `environmental.getClimateField` served for it --
 * see `src/components/map/AGENTS.md` §climate-field.
 *
 * ONE INSTANCE PER SIGNAL since 2026-08-10, where a single instance switched between nine
 * signals in place before. Each of the nine now has its own registry toggle, its own slider and
 * its own day, so there is no longer one climate field to be the current one: two rows can be
 * on at once showing two different days, and one mounted component cannot hold two answers.
 * Every id below is therefore derived from the signal rather than being a module constant.
 *
 * The instances stay composable because they do not all paint the same way. `renderForm`
 * decides the geometry the server sent and the layers drawn from it -- a tessellated wash,
 * dissolved filled bands over it, or points above both. Nine identical washes would be one
 * visible field and eight buried under it; see `ClimateRenderForm` in
 * lib/environmental/climate-field.ts.
 *
 * Plain MapLibre `fill`/`line`/`circle`, not deck.gl, for the same reason `SoilFieldLayer` is:
 * what reaches the browser is at most 512 features the server already resolved, and the
 * aggregation layers a `ContourLayer` would need are not a dependency -- both the tessellation
 * and the dissolve already happened server-side, in `parquet-climate-field.ts`.
 */
const EMPTY_COLLECTION: GeoJSON.FeatureCollection = {
  type: "FeatureCollection",
  features: [],
};

/**
 * Is there a PARSED style to add to?
 *
 * MapLibre v5's `Map.getStyle()` returns `undefined` before the Style object exists and
 * `Style.serialize()` itself returns `undefined` while `_loaded` is false -- neither throws, so a
 * bare call would in fact be safe (maplibre-gl/dist/maplibre-gl-dev.js, `getStyle()` and
 * `Style.serialize()`). It is wrapped anyway, in ONE place used by every gate in this file, because
 * serialize() walks live source/terrain objects and a map torn down under a pending effect is the
 * one case where that walk is not ours to reason about. Same helper on both gates: this file used
 * to guard the data effect and not the admission effect, which read as a disagreement about
 * whether getStyle() can throw.
 */
function hasParsedStyle(mapInstance: MapLibreMap): boolean {
  try {
    return Boolean(mapInstance.getStyle());
  } catch {
    return false;
  }
}

/**
 * Interpolated over the selected signal's own band table, so a fill and the panel's legend
 * cannot disagree about what a value looks like. Derived rather than restated, which is why
 * the assertion is needed: MapLibre types an expression as a union of fixed-length tuples and
 * a spread widens it -- the same trade `SoilFieldLayer` makes.
 */
function fillColorFor(signal: ClimateFieldSignalId): ExpressionSpecification {
  return [
    "interpolate",
    ["linear"],
    ["get", "value"],
    ...climateFieldColorStops(signal),
  ] as unknown as ExpressionSpecification;
}

/**
 * Point radius across the signal's own measured domain, in pixels.
 *
 * Both the size AND the colour carry the value, deliberately. Size alone is hard to read
 * against a busy basemap and impossible to match to a legend swatch; colour alone throws away
 * the one advantage a point form has over a fill, which is that magnitude survives being drawn
 * small. The domain is the signal's own p02-p98, so a 30 mm rainfall and a 2 mm one are
 * different marks rather than both saturating the top of a shared scale.
 */
function pointRadiusFor(signal: ClimateFieldSignalId): ExpressionSpecification {
  const { domainMinimum, domainMaximum } = climateFieldSignalDefinition(signal);
  return [
    "interpolate",
    ["linear"],
    ["get", "value"],
    domainMinimum,
    MINIMUM_POINT_RADIUS_PX,
    domainMaximum,
    MAXIMUM_POINT_RADIUS_PX,
  ] as unknown as ExpressionSpecification;
}

/**
 * The hairline around a filled cell, and DETAIL-RUNG ONLY -- see `zoomTier` below.
 *
 * Low even there, because a lattice outlined at full strength reads as a grid rather than as a
 * field. At a coarse rung it is not drawn at all: a stroke on every cell of a five-degree
 * tessellation is a mesh of block seams over the whole viewport, which is the second half of the
 * "nested blocks with visible seams" the 2026-09-01 assessment recorded.
 */
const OUTLINE_OPACITY = 0.2;

/**
 * Band boundaries are drawn at full weight, unlike the field form's hairline: they separate two
 * different values rather than two samples of one, and they have to stay legible over the fill
 * beneath them and over another signal's wash.
 */
const ISOLINE_WIDTH_PX = 1.6;

/** Smallest and largest point marks, in pixels, across a signal's measured domain. */
const MINIMUM_POINT_RADIUS_PX = 3;
const MAXIMUM_POINT_RADIUS_PX = 13;

/**
 * A dark hairline around each point mark.
 *
 * Needed only by this form: a filled cell is bounded by its neighbours, and a contour is a line
 * already, but a pale point over pale terrain -- a trace-rainfall drop, a low-wind mark -- has
 * nothing to separate it from the ground it sits on.
 */
const POINT_STROKE_COLOR = "#18181b";
const POINT_STROKE_WIDTH_PX = 0.8;

interface ClimateFieldLayerProps {
  map: MapLibreMap | null;
  /** Which quantity this instance draws. Fixed for the life of the instance's ids. */
  signal: ClimateFieldSignalId;
  /**
   * How it is painted. A change tears the layers down and rebuilds them, because the three
   * forms are different MapLibre layer types over different geometry -- the server sends
   * squares, dissolved bands or points depending on this, so a repaint in place is not possible.
   */
  renderForm: ClimateRenderForm;
  /**
   * The rung the served collection came from, or NULL while no collection has arrived.
   *
   * Two things depend on it and neither can be read off the geometry: the per-cell outline is
   * drawn ONLY at the detail rung, where a stroke separates two adjacent measurements rather than
   * tiling the viewport with block seams; and a rung change tears the layers down, so the map can
   * never hold one rung's cells under another's.
   *
   * Nullable since 2026-09-02, because the caller used to stand in with `BASE_ZOOM_TIER` before
   * any answer existed -- which is the one value that turns the outline ON, so a first collection
   * that arrived from z0 was stroked on every seam of a five-degree tessellation until the next
   * render corrected the rung. There is no honest default here: the rung is a property of the
   * ANSWER, and before the answer the only true statement is that it is not yet known.
   */
  zoomTier: ZoomTier | null;
  /**
   * The served collection. Empty -- never null -- when the layer is switched off or the
   * viewport holds none, so `setData` has something to clear with.
   */
  geojson?: GeoJSON.FeatureCollection | null;
  /** The fill's authored strength. The design value, not a control. */
  opacity?: number;
  /** The reader's MULTIPLIER for this signal's toggle, from `layer-store.layerOpacity`. */
  opacityScale?: number;
  visible?: boolean;
}

export function ClimateFieldLayer({
  map,
  signal,
  renderForm,
  zoomTier,
  geojson = null,
  opacity = 0.7,
  opacityScale = 1,
  visible = true,
}: ClimateFieldLayerProps) {
  const ids = useMemo(() => climateFieldLayerIdsFor(signal), [signal]);
  const paintColor = useMemo(() => fillColorFor(signal), [signal]);
  const pointRadius = useMemo(() => pointRadiusFor(signal), [signal]);
  // Both opacities go through the shared helper, even though both bases are plain numbers:
  // the multiplier rule then has ONE implementation rather than an inline product here and
  // the real rule in `layer-opacity.ts`. For a number the helper is exactly `base * factor`,
  // so this costs nothing and cannot drift from what the style-baked layers get.
  const fillOpacity = scaleOpacityValue(opacity, opacityScale) as number;
  const outlineOpacity = scaleOpacityValue(OUTLINE_OPACITY, opacityScale) as number;
  // Contours and points are drawn nearer full strength than a wash: both are thin marks a
  // reader has to pick out over whatever is beneath them, and 0.7 of a pale band colour on a
  // 1.6px line is close to invisible.
  const markOpacity = scaleOpacityValue(1, opacityScale) as number;
  // Latest props behind a ref so the style.load handler re-attaches with current values.
  const propsRef = useRef({
    geojson,
    paintColor,
    pointRadius,
    fillOpacity,
    outlineOpacity,
    markOpacity,
    renderForm,
    zoomTier,
    ids,
    signal,
    visible,
  });
  propsRef.current = {
    geojson,
    paintColor,
    pointRadius,
    fillOpacity,
    outlineOpacity,
    markOpacity,
    renderForm,
    zoomTier,
    ids,
    signal,
    visible,
  };

  const addLayers = useCallback((mapInstance: MapLibreMap) => {
    const {
      geojson: currentGeoJson,
      paintColor: currentPaintColor,
      pointRadius: currentPointRadius,
      fillOpacity: currentFillOpacity,
      outlineOpacity: currentOutlineOpacity,
      markOpacity: currentMarkOpacity,
      renderForm: currentRenderForm,
      zoomTier: currentZoomTier,
      ids: currentIds,
      signal: currentSignal,
    } = propsRef.current;
    const beforeId = getFirstSymbolLayer(mapInstance);

    if (!mapInstance.getSource(currentIds.sourceId)) {
      mapInstance.addSource(currentIds.sourceId, {
        type: "geojson",
        data: currentGeoJson ?? EMPTY_COLLECTION,
        attribution: CLIMATE_FIELD_ATTRIBUTION,
      });
    }

    const addValueLabels = () => {
      if (mapInstance.getLayer(currentIds.labelId)) return;
      mapInstance.addLayer(measuredValueLabelLayer({
        id: currentIds.labelId,
        source: currentIds.sourceId,
        unit: climateFieldSignalDefinition(currentSignal).unitLabel,
        fractionDigits: currentSignal.includes("wetness") ? 3 : 1,
        opacity: currentMarkOpacity,
      }), beforeId);
    };

    if (currentRenderForm === "field") {
      if (!mapInstance.getLayer(currentIds.fillId)) {
        mapInstance.addLayer(
          {
            id: currentIds.fillId,
            type: "fill",
            source: currentIds.sourceId,
            paint: { "fill-color": currentPaintColor, "fill-opacity": currentFillOpacity },
          },
          beforeId
        );
      }
      // DETAIL RUNG ONLY. At a coarse rung the tessellation covers the whole viewport, and a
      // stroke on every cell draws the grid instead of the field -- the block seams this track
      // exists to remove. The cells still abut exactly; nothing is left showing between them.
      if (
        currentZoomTier === BASE_ZOOM_TIER &&
        !mapInstance.getLayer(currentIds.outlineId)
      ) {
        mapInstance.addLayer(
          {
            id: currentIds.outlineId,
            type: "line",
            source: currentIds.sourceId,
            paint: {
              "line-color": "#3f3f46",
              "line-width": 0.5,
              "line-opacity": currentOutlineOpacity,
            },
          },
          beforeId
        );
      }
      addValueLabels();
      return;
    }

    if (currentRenderForm === "isoline") {
      // A `fill` UNDER the boundary line, where wave 1 stroked the boundaries and filled nothing.
      // The server sends dissolved isoBANDs -- closed areas, one per value class -- and the
      // track's acceptance gate is that a continuous field fills polygons rather than drawing
      // contour strokes only. The fill carries the band's own colour at the same strength the
      // `field` form uses, so a contoured signal still composes over a filled one at the reader's
      // opacity rather than at full weight.
      if (!mapInstance.getLayer(currentIds.isobandFillId)) {
        mapInstance.addLayer(
          {
            id: currentIds.isobandFillId,
            type: "fill",
            source: currentIds.sourceId,
            paint: { "fill-color": currentPaintColor, "fill-opacity": currentFillOpacity },
          },
          beforeId
        );
      }
      if (!mapInstance.getLayer(currentIds.isolineId)) {
        mapInstance.addLayer(
          {
            id: currentIds.isolineId,
            type: "line",
            source: currentIds.sourceId,
            layout: { "line-join": "round", "line-cap": "round" },
            paint: {
              "line-color": currentPaintColor,
              "line-width": ISOLINE_WIDTH_PX,
              "line-opacity": currentMarkOpacity,
            },
          },
          beforeId
        );
      }
      return;
    }

    if (!mapInstance.getLayer(currentIds.pointId)) {
      mapInstance.addLayer(
        {
          id: currentIds.pointId,
          type: "circle",
          source: currentIds.sourceId,
          paint: {
            "circle-color": currentPaintColor,
            "circle-radius": currentPointRadius,
            "circle-opacity": currentMarkOpacity,
            "circle-stroke-color": POINT_STROKE_COLOR,
            "circle-stroke-width": POINT_STROKE_WIDTH_PX,
            "circle-stroke-opacity": currentMarkOpacity,
          },
        },
        beforeId
      );
    }
    addValueLabels();
  }, []);

  const removeLayers = useCallback((mapInstance: MapLibreMap) => {
    const { ids: currentIds } = propsRef.current;
    // Every id, not just the current form's: this also runs when the form CHANGES, and the
    // layers being torn down are the previous form's. Removing only the current form's ids
    // would leave the old wash on the map under the new contours.
    safeRemoveLayerAndSource(
      mapInstance,
      [
        currentIds.labelId,
        currentIds.outlineId,
        currentIds.fillId,
        currentIds.isolineId,
        currentIds.isobandFillId,
        currentIds.pointId,
      ],
      currentIds.sourceId
    );
  }, []);

  // Persistent listener, never `once` alongside `on` -- see src/components/map/AGENTS.md
  // "Style.load listener order". This is what survives a basemap swap. It registers ONCE per
  // map: `visible`, `renderForm`, `zoomTier` and `ids` are deliberately absent from the deps,
  // because re-registering on every toggle or rung change would re-order this layer's handler
  // against every other layer's. Current visibility is read off propsRef at fire time; the
  // rebuild the form/rung/id changes need is the effect below, not this one.
  useEffect(() => {
    if (!map) return;

    const onStyleLoad = () => {
      if (propsRef.current.visible) addLayers(map);
    };
    map.on("style.load", onStyleLoad);

    return () => {
      map.off("style.load", onStyleLoad);
      removeLayers(map);
    };
  }, [map, addLayers, removeLayers]);

  // Admission AND rebuild.
  //
  // Admission: a PARSED style admits addSource/addLayer -- it does not have to be a LOADED one.
  // The old gate was `isStyleLoaded()`, which stays false until every unrelated source's tiles
  // land, so a late mount (after `style.load` had already fired) installed nothing and then only
  // ever saw `sourcedata`, whose handler updates sources that already exist. A genuinely
  // unparsed style falls through to the `style.load` listener above.
  //
  // Rebuild: `renderForm`, `zoomTier` and `ids` are dependencies because each changes what is
  // BUILT rather than what is painted -- a form change swaps the MapLibre layer type and the
  // geometry under it, a rung change swaps the cell size and whether the outline exists at all,
  // and a signal change swaps every id. None can be applied with setPaintProperty, so the
  // cleanup tears the previous build down (removeLayers covers every form's ids) and the body
  // rebuilds -- cheap, at 512 features and no tile fetch.
  useEffect(() => {
    if (!map) return;
    if (!visible) {
      removeLayers(map);
      return;
    }
    if (hasParsedStyle(map)) addLayers(map);
    return () => {
      removeLayers(map);
    };
  }, [map, visible, renderForm, zoomTier, ids, addLayers, removeLayers]);

  useEffect(() => {
    if (!map || !visible) return;
    if (!hasParsedStyle(map)) return;
    // setData rather than a re-add, so panning or a new day swaps this signal's features
    // without tearing the source down under the map. A missing source here is the legitimate
    // first-pass case: the style had not loaded, and addLayers creates it from propsRef with
    // this same data.
    const source = map.getSource(ids.sourceId) as GeoJSONSource | undefined;
    if (source) source.setData(geojson ?? EMPTY_COLLECTION);
    // The paint properties are written on update as well as at add time because the opacity
    // multiplier is a live control. The COLOUR ramp no longer needs this -- one instance draws
    // one signal for its whole life, so the ramp is fixed once the layer exists -- but it costs
    // nothing and keeps this block symmetric with the soil layers.
    if (map.getLayer(ids.fillId)) {
      map.setPaintProperty(ids.fillId, "fill-color", paintColor);
      map.setPaintProperty(ids.fillId, "fill-opacity", fillOpacity);
    }
    if (map.getLayer(ids.outlineId)) {
      map.setPaintProperty(ids.outlineId, "line-opacity", outlineOpacity);
    }
    if (map.getLayer(ids.isobandFillId)) {
      map.setPaintProperty(ids.isobandFillId, "fill-color", paintColor);
      map.setPaintProperty(ids.isobandFillId, "fill-opacity", fillOpacity);
    }
    if (map.getLayer(ids.isolineId)) {
      map.setPaintProperty(ids.isolineId, "line-color", paintColor);
      map.setPaintProperty(ids.isolineId, "line-opacity", markOpacity);
    }
    if (map.getLayer(ids.pointId)) {
      map.setPaintProperty(ids.pointId, "circle-color", paintColor);
      map.setPaintProperty(ids.pointId, "circle-radius", pointRadius);
      map.setPaintProperty(ids.pointId, "circle-opacity", markOpacity);
      map.setPaintProperty(ids.pointId, "circle-stroke-opacity", markOpacity);
    }
    if (map.getLayer(ids.labelId)) {
      map.setPaintProperty(ids.labelId, "text-opacity", markOpacity);
    }
  }, [
    map,
    ids,
    geojson,
    paintColor,
    pointRadius,
    fillOpacity,
    outlineOpacity,
    markOpacity,
    visible,
  ]);

  return null;
}
