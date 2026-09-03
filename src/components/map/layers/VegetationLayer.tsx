"use client";

import { useEffect, useRef, useCallback } from "react";
import type { Map as MapLibreMap, GeoJSONSource, RasterTileSource } from "maplibre-gl";
import {
  GIBS_NDVI_PRODUCT,
  getEnvironmentalTileTemplate,
  getNDVITileUrl,
  getNDWITileUrl,
  NDVI_COLOR_RAMP,
} from "@/lib/vegetation";
import { getFirstSymbolLayer, safeRemoveLayerAndSource } from "@/lib/map/layer-utils";
import { useVegetationStore } from "@/stores/vegetation-store";
import type { ExpressionSpecification } from "@/types/map";

export type VegetationMode = "ndvi" | "ndwi" | "nbr";

/**
 * Which of the two NDVI encodings draws. They are alternative views of the SAME quantity at
 * different provenance and resolution -- the 0.25-degree cells this platform measured, or the
 * global MODIS/Terra 8-day composite proxied from NASA GIBS -- so they are radio-selectable,
 * never independently checkable. Painting both stacked the measured grid over the composite at
 * the same alpha, which is what made the layer read as a double-render glitch.
 */
export type VegetationSource = "measured" | "satellite";

type LayerVisibility = "visible" | "none";

interface NdviEncodingVisibility {
  satelliteRaster: LayerVisibility;
  measuredCells: LayerVisibility;
}

/**
 * The one place the exclusivity rule is written, so the attach path and the update effect
 * cannot drift apart -- the first style change is exactly where a duplicated predicate would
 * have desynced them.
 *
 * Layout visibility rather than a zeroed opacity is the gate on purpose: MapLibre requests no
 * tiles for a source whose every layer is hidden, so the unselected encoding costs no
 * bandwidth, and `opacity` is left meaning only "how strong" -- which is what the panel's
 * slider moves. The switch is exhaustive: a third source member fails to compile here rather
 * than silently drawing nothing.
 */
function ndviEncodingVisibility(
  mode: VegetationMode,
  source: VegetationSource
): NdviEncodingVisibility {
  if (mode !== "ndvi") return { satelliteRaster: "none", measuredCells: "none" };
  switch (source) {
    case "satellite":
      return { satelliteRaster: "visible", measuredCells: "none" };
    case "measured":
      return { satelliteRaster: "none", measuredCells: "visible" };
  }
}

interface VegetationLayerProps {
  map: MapLibreMap | null;
  /**
   * Measured NDVI supports read from the warehouse, as the 0.25-degree cells the platform
   * actually observed.
   *
   * `presentParquetVegetation` builds each square from the observation's own declared envelope
   * and emits a Point only where no envelope arrived. Until 2026-09-02 it emitted a centre Point
   * for EVERY observation and this component drew it as a zoom-scaled circle -- the `raw_point`
   * deviation `LAYER_RENDER_CONTRACT.vegetation` recorded, whose radius said nothing about the
   * ground the reading covers.
   */
  geojson?: GeoJSON.FeatureCollection | null;
  mode?: VegetationMode;
  /**
   * Composite period of the GIBS raster, projected from the time slider's day by
   * `useVegetationDisplayMode` -- never independent vegetation state. Null means "no day yet"
   * (capabilities have not landed): the raster is then not attached at all rather than
   * guessed from the browser clock, which is a different clock than the server's.
   */
  year?: number | null;
  month?: number | null;
  ndviMode?: "absolute" | "anomaly";
  showNDWI?: boolean;
  /** The authored strength of every raster and the measured-cell fill. Not a control. */
  opacity?: number;
  /**
   * The reader's MULTIPLIER over `opacity`, from `layer-store.layerOpacity.vegetation`.
   *
   * It threads THROUGH this component rather than being written from outside because the
   * layout-visibility gating in `ndviEncodingVisibility` is semantic: it is how the layer
   * switches between the measured cells and the GIBS composite without adding and removing
   * sources. An external writer walking every style layer would fight that gating and could
   * not know which encoding is meant to be dark.
   */
  opacityScale?: number;
  visible?: boolean;
}

const NDVI_LAYER_ID = "ndvi-overlay-layer";
const NDWI_LAYER_ID = "ndwi-overlay-layer";
const NBR_LAYER_ID = "nbr-recovery-layer";
const NDVI_CELL_SOURCE_ID = "vegetation-ndvi-cells";
const NDVI_CELL_FILL_LAYER_ID = "vegetation-ndvi-cells-fill";
const NDVI_CELL_OUTLINE_LAYER_ID = "vegetation-ndvi-cells-outline";

/** Cell-boundary cue, deliberately independent of the reader's opacity. */
const CELL_OUTLINE_OPACITY = 0.35;

/**
 * The outline fades out below the middle band and is fully off across the coarse one.
 *
 * It exists to keep the cells legible as discrete 0.25-degree samples, and at z9 and above that
 * is what it does. Zoomed out, a 0.25-degree cell is a handful of pixels wide, so a stroke on
 * every shared edge is most of the cell: the lattice reads as a grid of seams over the field
 * rather than as the field, which is the "nested blocks with visible seams" artefact the
 * 2026-09-01 assessment found on the ERA5 soil layers. Neighbouring cells share bit-identical
 * boundaries (`supportCellPolygon`), so removing the stroke opens no cracks.
 */
const NDVI_CELL_OUTLINE_OPACITY = [
  "interpolate", ["linear"], ["zoom"],
  0, 0,
  8, 0,
  9, CELL_OUTLINE_OPACITY,
] as unknown as ExpressionSpecification;

const EMPTY_CELL_COLLECTION: GeoJSON.FeatureCollection = {
  type: "FeatureCollection",
  features: [],
};

/**
 * The GIBS tile template for a composite period, or "" when there is no period to request or
 * the period is outside what GIBS publishes. One helper so attach and update cannot disagree
 * about which periods are requestable.
 */
function ndviTemplateFor(
  year: number | null,
  month: number | null,
  ndviMode: "absolute" | "anomaly"
): string {
  if (year === null || month === null) return "";
  return getNDVITileUrl(year, month, ndviMode);
}

/**
 * Fill colour interpolated over the shared NDVI ramp, so a cell and the legend below it
 * cannot disagree about what a value looks like.
 *
 * Derived from NDVI_COLOR_RAMP rather than restated, which is what forces the assertion:
 * MapLibre types an expression as a union of fixed-length tuples, and spreading a mapped
 * array widens it to `(string | number | string[])[]`. Restating the nine stops inline
 * would let the literal be contextually typed -- and would be the first place the fill and
 * the legend drift apart.
 */
const NDVI_CELL_FILL_COLOR = [
  "interpolate",
  ["linear"],
  ["get", "ndvi"],
  ...NDVI_COLOR_RAMP.flatMap((stop) => [stop.value, stop.color]),
] as unknown as ExpressionSpecification;

export function VegetationLayer({
  map,
  geojson = null,
  mode = "ndvi",
  // No browser-clock default: the composite period is the slider's day projected onto a
  // month, and until capabilities name a day there is honestly no period to draw.
  year = null,
  month = null,
  ndviMode = "absolute",
  showNDWI = false,
  opacity = 0.75,
  opacityScale = 1,
  visible = true,
}: VegetationLayerProps) {
  // Read straight from the store rather than as a prop: the chosen encoding is display state
  // only this renderer consumes, and `LayerManager` has nothing to add to it on the way past.
  // Same shape as RouteLayer's `useRoutingStore`. When the layer-pivot panel lands, this can
  // move onto `useVegetationDisplayMode` beside `mode`/`opacity` if a second consumer appears.
  const source = useVegetationStore((state) => state.source);

  // The one value every paint below is written from: the authored strength times the reader's
  // multiplier. Computed once here so the attach path and the update effect cannot drift.
  const drawnOpacity = opacity * opacityScale;

  // Keep latest prop values in refs so the style.load handler always uses current values
  const propsRef = useRef({
    geojson,
    mode,
    source,
    year,
    month,
    ndviMode,
    showNDWI,
    drawnOpacity,
    visible,
  });
  propsRef.current = {
    geojson,
    mode,
    source,
    year,
    month,
    ndviMode,
    showNDWI,
    drawnOpacity,
    visible,
  };

  const addAllLayers = useCallback((m: MapLibreMap) => {
    const {
      geojson,
      mode,
      source,
      year,
      month,
      ndviMode,
      showNDWI,
      drawnOpacity,
    } = propsRef.current;
    const beforeId = getFirstSymbolLayer(m);
    const { satelliteRaster, measuredCells } = ndviEncodingVisibility(mode, source);
    const ndviTileUrl = ndviTemplateFor(year, month, ndviMode);
    const ndwiTileUrl = year === null || month === null ? "" : getNDWITileUrl(year, month);
    const nbrTileUrl = getEnvironmentalTileTemplate(
      "vegetation/nbr/latest/{z}/{x}/{y}.png"
    );

    // Each product attaches independently. NDWI has no upstream at all (GIBS
    // publishes no water-index raster) and NBR is unpublished, so an
    // all-or-nothing guard here would suppress NDVI, which is real and served.

    // --- NDVI ---
    if (ndviTileUrl && !m.getSource("ndvi-overlay")) {
      m.addSource("ndvi-overlay", {
        type: "raster",
        tiles: [ndviTileUrl],
        tileSize: 256,
        // GIBS publishes this product no deeper than z9 and the proxy 404s past it. Without
        // an explicit maxzoom MapLibre assumes 22 and requests z10+ tiles, so the raster
        // silently vanished the moment you zoomed past 9 while the measured cells kept
        // drawing -- the same toggle showing two different pictures depending on zoom.
        // Declaring it makes MapLibre overzoom the z9 tile instead.
        maxzoom: GIBS_NDVI_PRODUCT.maxZoom,
        attribution: "NASA GIBS / Copernicus",
      });
    }
    if (ndviTileUrl && !m.getLayer(NDVI_LAYER_ID)) {
      m.addLayer({
        id: NDVI_LAYER_ID,
        type: "raster",
        source: "ndvi-overlay",
        layout: { visibility: satelliteRaster },
        paint: { "raster-opacity": drawnOpacity },
      }, beforeId);
    }

    // --- NDWI ---
    if (ndwiTileUrl && !m.getSource("ndwi-overlay")) {
      m.addSource("ndwi-overlay", {
        type: "raster",
        tiles: [ndwiTileUrl],
        tileSize: 256,
        attribution: "NASA GIBS",
      });
    }
    if (ndwiTileUrl && !m.getLayer(NDWI_LAYER_ID)) {
      m.addLayer({
        id: NDWI_LAYER_ID,
        type: "raster",
        source: "ndwi-overlay",
        layout: { visibility: showNDWI && mode === "ndwi" ? "visible" : "none" },
        paint: { "raster-opacity": drawnOpacity },
      }, beforeId);
    }

    // --- NBR ---
    if (nbrTileUrl && !m.getSource("nbr-recovery")) {
      m.addSource("nbr-recovery", {
        type: "raster",
        tiles: [nbrTileUrl],
        tileSize: 256,
      });
    }
    if (nbrTileUrl && !m.getLayer(NBR_LAYER_ID)) {
      m.addLayer({
        id: NBR_LAYER_ID,
        type: "raster",
        source: "nbr-recovery",
        layout: { visibility: mode === "nbr" ? "visible" : "none" },
        paint: { "raster-opacity": drawnOpacity },
      }, beforeId);
    }

    // --- Measured NDVI grid cells (environmental.getVegetationIndex) ---
    // These are the readings this platform ingested and can cite a scene for; the GIBS raster
    // above is a global composite it merely proxies. They are ALTERNATIVES, not a stack:
    // `ndviEncodingVisibility` shows exactly one, so the ordering below no longer decides what
    // a reader sees. It is kept last only so that a future "both" option -- if one is ever
    // justified -- would still put the measurements above the composite rather than under it.
    // The outline is deliberate: it keeps the cells legible as discrete 0.25-degree samples
    // rather than as a continuous surface the sampling never produced -- but only from the
    // middle band up; see NDVI_CELL_OUTLINE_OPACITY for why it fades out below that.
    //
    // Fill and outline both filter to Polygon, and there is no longer a circle layer beside
    // them: `presentParquetVegetation` draws every observation as the square its envelope
    // declares. A Point survives only where no envelope arrived at all, and such an observation
    // is deliberately left UNDRAWN rather than shown as a dot -- a dot is the `raw_point`
    // deviation this slice closed, and one drawn at a coarser rung would look exactly like a
    // measurement whose footprint the platform knows.
    if (!m.getSource(NDVI_CELL_SOURCE_ID)) {
      m.addSource(NDVI_CELL_SOURCE_ID, {
        type: "geojson",
        data: geojson ?? EMPTY_CELL_COLLECTION,
      });
    }
    if (!m.getLayer(NDVI_CELL_FILL_LAYER_ID)) {
      m.addLayer({
        id: NDVI_CELL_FILL_LAYER_ID,
        type: "fill",
        source: NDVI_CELL_SOURCE_ID,
        filter: ["==", ["geometry-type"], "Polygon"],
        layout: { visibility: measuredCells },
        paint: {
          "fill-color": NDVI_CELL_FILL_COLOR,
          "fill-opacity": drawnOpacity,
        },
      }, beforeId);
    }
    if (!m.getLayer(NDVI_CELL_OUTLINE_LAYER_ID)) {
      m.addLayer({
        id: NDVI_CELL_OUTLINE_LAYER_ID,
        type: "line",
        source: NDVI_CELL_SOURCE_ID,
        filter: ["==", ["geometry-type"], "Polygon"],
        layout: { visibility: measuredCells },
        paint: {
          "line-color": "#1b3a1b",
          "line-width": 0.5,
          // Zoom-gated, never tied to the slider: the outline is a cell-boundary cue, so it has
          // to stay readable at the low end of the opacity range the fill follows.
          "line-opacity": NDVI_CELL_OUTLINE_OPACITY,
        },
      }, beforeId);
    }
  }, []);

  const removeAllLayers = useCallback((m: MapLibreMap) => {
    safeRemoveLayerAndSource(m, [NDVI_LAYER_ID], "ndvi-overlay");
    safeRemoveLayerAndSource(m, [NDWI_LAYER_ID], "ndwi-overlay");
    safeRemoveLayerAndSource(m, [NBR_LAYER_ID], "nbr-recovery");
    safeRemoveLayerAndSource(
      m,
      [NDVI_CELL_FILL_LAYER_ID, NDVI_CELL_OUTLINE_LAYER_ID],
      NDVI_CELL_SOURCE_ID
    );
  }, []);

  // Main effect: add/remove layers and listen for style changes
  useEffect(() => {
    if (!map) return;

    if (!visible) {
      removeAllLayers(map);
      return;
    }

    // Handler that re-adds all layers after a style change
    const onStyleLoad = () => {
      if (!propsRef.current.visible) return;
      addAllLayers(map);
    };

    // Add now if the style is ready; the persistent listener covers both the
    // first load and every later swap. See src/components/map/AGENTS.md
    // "Style.load listener order" -- no `once` alongside `on`.
    if (map.isStyleLoaded()) addAllLayers(map);
    map.on("style.load", onStyleLoad);

    return () => {
      map.off("style.load", onStyleLoad);
      removeAllLayers(map);
    };
  }, [map, visible, addAllLayers, removeAllLayers]);

  // Update tile URLs, visibility and opacity when the composite period, the selected source,
  // the mode or the opacity change. `year` and `month` move only when the slider's day crosses
  // a month boundary (they are memoized on that pair upstream), so a day-granular scrub inside
  // one month never re-requests tiles.
  useEffect(() => {
    if (!map || !visible) return;
    try {
      if (!map.getStyle()) return;
    } catch {
      return;
    }

    // Same resolver the attach path uses, so a style swap cannot leave the two encodings
    // disagreeing about which one is on.
    const { satelliteRaster, measuredCells } = ndviEncodingVisibility(mode, source);

    // NDVI tile URL + visibility + opacity
    const ndviSource = map.getSource("ndvi-overlay") as RasterTileSource | undefined;
    const ndviTileUrl = ndviTemplateFor(year, month, ndviMode);
    if (ndviSource && ndviTileUrl) {
      ndviSource.setTiles([ndviTileUrl]);
    }
    if (map.getLayer(NDVI_LAYER_ID)) {
      map.setLayoutProperty(NDVI_LAYER_ID, "visibility", satelliteRaster);
      map.setPaintProperty(NDVI_LAYER_ID, "raster-opacity", drawnOpacity);
    }

    // NDWI tile URL + visibility + opacity
    const ndwiSource = map.getSource("ndwi-overlay") as RasterTileSource | undefined;
    const ndwiTileUrl = year === null || month === null ? "" : getNDWITileUrl(year, month);
    if (ndwiSource && ndwiTileUrl) {
      ndwiSource.setTiles([ndwiTileUrl]);
    }
    if (map.getLayer(NDWI_LAYER_ID)) {
      map.setLayoutProperty(
        NDWI_LAYER_ID,
        "visibility",
        showNDWI && mode === "ndwi" ? "visible" : "none"
      );
      map.setPaintProperty(NDWI_LAYER_ID, "raster-opacity", drawnOpacity);
    }

    // NBR visibility + opacity (tile URL is static)
    if (map.getLayer(NBR_LAYER_ID)) {
      map.setLayoutProperty(NBR_LAYER_ID, "visibility", mode === "nbr" ? "visible" : "none");
      map.setPaintProperty(NBR_LAYER_ID, "raster-opacity", drawnOpacity);
    }

    // Measured NDVI cells: new viewport data, then the source gate and opacity. setData
    // rather than a re-add, so panning swaps the cells without tearing the source down under
    // the map. The source can legitimately be missing here on the first pass -- the style had
    // not loaded when this ran -- and addAllLayers then creates it from propsRef with the
    // same data.
    const cellSource = map.getSource(NDVI_CELL_SOURCE_ID) as GeoJSONSource | undefined;
    if (cellSource) cellSource.setData(geojson ?? EMPTY_CELL_COLLECTION);
    if (map.getLayer(NDVI_CELL_FILL_LAYER_ID)) {
      map.setLayoutProperty(NDVI_CELL_FILL_LAYER_ID, "visibility", measuredCells);
      map.setPaintProperty(NDVI_CELL_FILL_LAYER_ID, "fill-opacity", drawnOpacity);
    }
    if (map.getLayer(NDVI_CELL_OUTLINE_LAYER_ID)) {
      map.setLayoutProperty(NDVI_CELL_OUTLINE_LAYER_ID, "visibility", measuredCells);
      map.setPaintProperty(NDVI_CELL_OUTLINE_LAYER_ID, "line-opacity", NDVI_CELL_OUTLINE_OPACITY);
    }
  }, [
    map,
    geojson,
    year,
    month,
    ndviMode,
    mode,
    source,
    showNDWI,
    drawnOpacity,
    visible,
  ]);

  return null;
}
