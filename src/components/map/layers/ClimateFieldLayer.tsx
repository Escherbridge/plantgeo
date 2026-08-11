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
import { useStyleReady } from "@/components/map/layers/use-style-ready";
import { scaleOpacityValue } from "@/lib/map/layer-opacity";
import type { ExpressionSpecification } from "@/types/map";

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
 * decides the geometry the server sent and the layers drawn from it -- a filled wash, contours
 * across it, or points above both. Nine fills would be one visible field and eight buried
 * under it; see `ClimateRenderForm` in lib/environmental/climate-field.ts.
 *
 * Plain MapLibre `fill`/`line`/`circle`, not deck.gl, for the same reason `SoilFieldLayer` is:
 * what reaches the browser is at most 512 features the server already resolved, and the
 * aggregation layers a `ContourLayer` would need are not a dependency -- the contouring already
 * happened server-side, in `climateFieldIsolineFeatures`.
 */
const EMPTY_COLLECTION: GeoJSON.FeatureCollection = {
  type: "FeatureCollection",
  features: [],
};

/** Every id one signal's instance owns. Derived, so two instances can never collide. */
function layerIdsFor(signal: ClimateFieldSignalId) {
  const sourceId = `climate-field-${signal}`;
  return {
    sourceId,
    fillId: `${sourceId}-fill`,
    outlineId: `${sourceId}-outline`,
    isolineId: `${sourceId}-isoline`,
    pointId: `${sourceId}-point`,
  };
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
 * A scalar, where `SoilFieldLayer`'s is a `case` expression: every feature the FIELD form
 * serves is an unaggregated 0.5-degree cell, so the outline always says something true and
 * there is no isoband contour to suppress. Low, because a lattice outlined at full strength
 * reads as a grid rather than as a field.
 */
const OUTLINE_OPACITY = 0.2;

/**
 * Contours are drawn at full weight, unlike the field form's hairline outline: they are the
 * whole of what that form draws, and they have to stay legible over another signal's wash.
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
   * squares, contours or points depending on this, so a repaint in place is not possible.
   */
  renderForm: ClimateRenderForm;
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
  geojson = null,
  opacity = 0.7,
  opacityScale = 1,
  visible = true,
}: ClimateFieldLayerProps) {
  const ids = useMemo(() => layerIdsFor(signal), [signal]);
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
    ids,
  });
  propsRef.current = {
    geojson,
    paintColor,
    pointRadius,
    fillOpacity,
    outlineOpacity,
    markOpacity,
    renderForm,
    ids,
  };
  const styleReady = useStyleReady(map);

  const addLayers = useCallback((mapInstance: MapLibreMap) => {
    const {
      geojson: currentGeoJson,
      paintColor: currentPaintColor,
      pointRadius: currentPointRadius,
      fillOpacity: currentFillOpacity,
      outlineOpacity: currentOutlineOpacity,
      markOpacity: currentMarkOpacity,
      renderForm: currentRenderForm,
      ids: currentIds,
    } = propsRef.current;
    const beforeId = getFirstSymbolLayer(mapInstance);

    if (!mapInstance.getSource(currentIds.sourceId)) {
      mapInstance.addSource(currentIds.sourceId, {
        type: "geojson",
        data: currentGeoJson ?? EMPTY_COLLECTION,
        attribution: CLIMATE_FIELD_ATTRIBUTION,
      });
    }

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
      if (!mapInstance.getLayer(currentIds.outlineId)) {
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
      return;
    }

    if (currentRenderForm === "isoline") {
      // A `line` over POLYGON features, and no `fill` beside it. The server sends dissolved
      // isobands; stroking their boundaries and filling nothing is what makes this form a
      // contour map rather than a second wash, and it is the whole reason a contoured signal
      // composes over a filled one.
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
  }, []);

  const removeLayers = useCallback((mapInstance: MapLibreMap) => {
    const { ids: currentIds } = propsRef.current;
    // Every id, not just the current form's: this also runs when the form CHANGES, and the
    // layers being torn down are the previous form's. Removing only the current form's ids
    // would leave the old wash on the map under the new contours.
    safeRemoveLayerAndSource(
      mapInstance,
      [currentIds.outlineId, currentIds.fillId, currentIds.isolineId, currentIds.pointId],
      currentIds.sourceId
    );
  }, []);

  // Persistent listener, never `once` alongside `on` -- see src/components/map/AGENTS.md
  // "Style.load listener order". This is what survives a basemap swap.
  //
  // `renderForm` and `ids` are dependencies because both change what is BUILT rather than what
  // is painted: a form change swaps the MapLibre layer type and the geometry under it, and a
  // signal change swaps every id. Neither can be applied with setPaintProperty, so both tear
  // down and rebuild -- cheap, at 512 features and no tile fetch.
  useEffect(() => {
    if (!map) return;
    if (!visible) {
      removeLayers(map);
      return;
    }
    const onStyleLoad = () => addLayers(map);
    if (map.isStyleLoaded()) addLayers(map);
    map.on("style.load", onStyleLoad);
    return () => {
      map.off("style.load", onStyleLoad);
      removeLayers(map);
    };
  }, [map, visible, renderForm, ids, addLayers, removeLayers]);

  // The mount-time race the persistent listener above cannot catch: if the current style had
  // already finished loading when this component mounted, no further `style.load` arrives and
  // nothing retries. `styleReady` is a trigger, not a gate -- it can be a tick stale
  // mid-render, so the live `isStyleLoaded()` below is what decides. `addLayers` is
  // idempotent, so the overlap with the effect above is a no-op rather than a throw.
  useEffect(() => {
    if (!map || !visible) return;
    if (!map.isStyleLoaded()) return;
    addLayers(map);
  }, [map, visible, styleReady, addLayers]);

  useEffect(() => {
    if (!map || !visible) return;
    try {
      if (!map.getStyle()) return;
    } catch {
      return;
    }
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
