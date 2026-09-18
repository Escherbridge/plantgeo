"use client";

import { useEffect, useRef, useCallback } from "react";
import type { Map as MapLibreMap, GeoJSONSource, MapMouseEvent } from "maplibre-gl";
import { getFirstSymbolLayer, safeRemoveLayerAndSource } from "@/lib/map/layer-utils";
import {
  BOTANICAL_DETAIL_MIN_ZOOM,
  type BotanicalOccurrenceFeature,
  type BotanicalSupportBand,
} from "@/lib/botanical-occurrences";
import { isProvisionalBotanicalCollection } from "@/lib/environmental/botanical-governance-status";

const SOURCE_ID = "botanical-occurrences";
const LAYER_ID_EXACT = "botanical-occurrences-exact";
const LAYER_ID_GENERALIZED = "botanical-occurrences-generalized";
const LAYER_ID_POSSIBLE = "botanical-occurrences-possible";
const LAYER_ID_PROVISIONAL_RING = "botanical-occurrences-provisional-ring";

/**
 * Circle-radius/style legend for the detail layer. Confirmed-exact records draw the smallest,
 * most saturated dot -- the tightest coordinate support gets the strongest visual claim.
 * `possible`-membership records draw as a distinct hollow-ring style at every zoom so an
 * uncertain determination is never visually indistinguishable from an admitted one, matching
 * the spec's "distinct style for membership: possible" requirement.
 */
export const BOTANICAL_OCCURRENCE_LEGEND = [
  { label: "Confirmed, exact locality", color: "#5ec26a" },
  { label: "Confirmed, generalized locality", color: "#2f7d3a" },
  { label: "Possible determination", color: "#e0c341" },
  { label: "Provisional: not yet governance-admitted", color: "#e8813f" },
] as const;

/** Circle-radius of the outer provisional ring; drawn slightly larger than the widest base dot. */
const PROVISIONAL_RING_RADIUS = 9;

/** A specimen record has no coordinates to place; the caller must never synthesize a centroid. */
function isSpatial(
  feature: BotanicalOccurrenceFeature
): feature is BotanicalOccurrenceFeature & { longitude: number; latitude: number } {
  return Number.isFinite(feature.longitude) && Number.isFinite(feature.latitude);
}

/**
 * Builds the GeoJSON this layer draws from raw wire features, dropping nonspatial records.
 * Exported so the surrounding data hook and the test suite can both build the same shape
 * without duplicating the spatial guard.
 *
 * The first four properties are what the PAINT FILTERS key on. The rest are PROVENANCE, carried
 * because the shared hover manager (`lib/map/hover-fields.ts`) reads MapLibre feature properties
 * and nothing else -- a tooltip cannot reach back into the response -- and a specimen dot whose
 * source and rights are not reachable on hover is an unattributed use of somebody's collection.
 * `publishedAt` is the RESPONSE's publication timestamp rather than a per-feature field, threaded
 * in by the caller so a reader can see how stale the generation they are looking at is.
 */
export function botanicalOccurrencesToGeoJSON(
  features: BotanicalOccurrenceFeature[],
  publishedAt: string | null = null
): GeoJSON.FeatureCollection {
  return {
    type: "FeatureCollection",
    features: features.filter(isSpatial).map((feature) => ({
      type: "Feature",
      geometry: { type: "Point", coordinates: [feature.longitude, feature.latitude] },
      properties: {
        occurrence_id: feature.occurrence_id,
        spatial_class: feature.spatial_class,
        membership: feature.membership,
        resolution_state: feature.resolution_state,
        provisional: isProvisionalBotanicalCollection(feature.collection_key),
        scientific_name: feature.scientific_name,
        family: feature.family,
        collection_key: feature.collection_key,
        catalog_number: feature.catalog_number,
        recorded_by: feature.recorded_by,
        event_start: feature.event_interval.start,
        event_end: feature.event_interval.end,
        event_precision: feature.event_interval.precision,
        coordinate_uncertainty_m: feature.coordinate_uncertainty_m,
        rights_uri: feature.rights_uri,
        attribution_text: feature.attribution_text,
        published_at: publishedAt,
      },
    })),
  };
}

/**
 * A one-line caption for whatever `useBotanicalOccurrences` currently reports, or null when the
 * layer is simply drawing what it was asked to draw.
 *
 * Exported beside the layer rather than left to each mounting surface: a reader looking at a
 * specimen map must be able to tell "this generation holds nothing here" from "the read failed"
 * from "you are looking at the previous viewport", and three call sites inventing three wordings
 * for that is how one of them ends up silently omitting the distinction. `partial` in particular is
 * NOT an error -- the plane bounded the answer and said so -- and must read that way.
 */
export function describeBotanicalOccurrencesState(snapshot: {
  phase: "idle" | "loading" | "success" | "empty" | "error";
  isStale: boolean;
  isPartial: boolean;
  error: { reason: string; detail?: string } | null;
  band?: BotanicalSupportBand;
  servingBand?: BotanicalSupportBand | null;
}): string | null {
  const sentences = [describeReadState(snapshot), describeServingRung(snapshot)].filter(
    (sentence): sentence is string => sentence !== null
  );
  return sentences.length === 0 ? null : sentences.join(" ");
}

function describeReadState(snapshot: {
  phase: "idle" | "loading" | "success" | "empty" | "error";
  isStale: boolean;
  isPartial: boolean;
  error: { reason: string; detail?: string } | null;
}): string | null {
  if (snapshot.phase === "error") {
    return snapshot.error === null
      ? "Specimen records could not be loaded."
      : `Specimen records could not be loaded (${snapshot.error.reason}).`;
  }
  if (snapshot.phase === "loading") {
    return snapshot.isStale ? "Loading specimen records for this view; showing the previous one." : "Loading specimen records…";
  }
  if (snapshot.phase === "empty") return "This release holds no specimen records in this view.";
  if (snapshot.isPartial) return "More specimen records match this view than are drawn.";
  return null;
}

/** How each rung reads in a sentence; `detail` is points, the other two are published cell grids. */
const BOTANICAL_BAND_LABEL: Readonly<Record<BotanicalSupportBand, string>> = {
  detail: "individual specimen points",
  "grid-0.05": "the grid-0.05 support rung",
  "grid-0.25": "the grid-0.25 support rung",
};

/**
 * Says so when a WIDER RUNG answered than the zoom asked for.
 *
 * Since the owner decision of 2026-09-18 a viewport too wide for its zoom's own rung is served
 * from the next rung out instead of being refused (`botanicalServingBandForViewport`). That is a
 * substitution of evidence -- cells where points were asked for, or coarser cells than expected --
 * and a reader who is not told has no way to know the drawing changed meaning. Silent when the
 * served rung is the requested one, which is the ordinary case.
 */
function describeServingRung(snapshot: {
  band?: BotanicalSupportBand;
  servingBand?: BotanicalSupportBand | null;
}): string | null {
  const { band, servingBand } = snapshot;
  if (band === undefined || servingBand === undefined || servingBand === null) return null;
  if (servingBand === band) return null;
  return `Showing ${BOTANICAL_BAND_LABEL[servingBand]}: this view is wider than ${BOTANICAL_BAND_LABEL[band]} can answer.`;
}

interface BotanicalOccurrencesLayerProps {
  map: MapLibreMap | null;
  geojson: GeoJSON.FeatureCollection | null;
  zoom: number;
  visible?: boolean;
  onSelectFeature?: (occurrenceId: string) => void;
  /**
   * The read's semantic state, when the mounting surface has one to pass.
   *
   * OPTIONAL and defaulted so the existing `LayerManager` mount keeps compiling untouched. Its only
   * drawing effect is that `"error"` removes the layers: retaining a drawn collection under a
   * failed read is a false claim about the current viewport, which is the same rule
   * `useViewportProxiedLayers`'s `keepPreviousData` note states ("it retains across a pending
   * request, NOT across a failure"). A pending read deliberately keeps drawing -- geometry in hand
   * is still true where it is.
   */
  readPhase?: "idle" | "loading" | "success" | "empty" | "error";
}

/**
 * Detail-zoom specimen occurrence points. Only mounted at `zoom >= BOTANICAL_DETAIL_MIN_ZOOM`
 * by the caller -- this component draws unconditionally once handed geojson, so the zoom-band
 * exclusivity with `BotanicalRichnessLayer` lives in whichever container chooses which layer
 * to mount, matching the pattern the other layer components in this directory use for their
 * own zoom/visibility gates.
 */
export function BotanicalOccurrencesLayer({
  map,
  geojson,
  zoom,
  visible = true,
  onSelectFeature,
  readPhase = "success",
}: BotanicalOccurrencesLayerProps) {
  // A failed read is treated exactly as "nothing to draw": the layers come down rather than keep
  // asserting the last collection under an error the reader is being shown elsewhere.
  const drawable = readPhase !== "error";
  const propsRef = useRef({ geojson, visible, zoom, drawable });
  propsRef.current = { geojson, visible, zoom, drawable };
  const onSelectFeatureRef = useRef(onSelectFeature);
  onSelectFeatureRef.current = onSelectFeature;

  const addLayers = useCallback((m: MapLibreMap) => {
    const { geojson } = propsRef.current;
    if (!geojson) return;
    const beforeId = getFirstSymbolLayer(m);

    if (!m.getSource(SOURCE_ID)) {
      m.addSource(SOURCE_ID, { type: "geojson", data: geojson });
    } else {
      (m.getSource(SOURCE_ID) as GeoJSONSource).setData(geojson);
    }

    if (!m.getLayer(LAYER_ID_EXACT)) {
      m.addLayer(
        {
          id: LAYER_ID_EXACT,
          type: "circle",
          source: SOURCE_ID,
          filter: ["all", ["==", ["get", "membership"], "confirmed"], ["==", ["get", "spatial_class"], "exact"]],
          paint: {
            "circle-radius": 4,
            "circle-color": BOTANICAL_OCCURRENCE_LEGEND[0].color,
            "circle-stroke-width": 1,
            "circle-stroke-color": "#0a1f0d",
          },
        },
        beforeId
      );
    }
    if (!m.getLayer(LAYER_ID_GENERALIZED)) {
      m.addLayer(
        {
          id: LAYER_ID_GENERALIZED,
          type: "circle",
          source: SOURCE_ID,
          filter: ["all", ["==", ["get", "membership"], "confirmed"], ["==", ["get", "spatial_class"], "generalized"]],
          paint: {
            "circle-radius": 8,
            "circle-color": BOTANICAL_OCCURRENCE_LEGEND[1].color,
            "circle-opacity": 0.45,
            "circle-stroke-width": 1,
            "circle-stroke-color": "#0a1f0d",
          },
        },
        beforeId
      );
    }
    if (!m.getLayer(LAYER_ID_POSSIBLE)) {
      m.addLayer(
        {
          id: LAYER_ID_POSSIBLE,
          type: "circle",
          source: SOURCE_ID,
          filter: ["==", ["get", "membership"], "possible"],
          paint: {
            "circle-radius": 5,
            "circle-color": "transparent",
            "circle-stroke-width": 2,
            "circle-stroke-color": BOTANICAL_OCCURRENCE_LEGEND[2].color,
            "circle-stroke-opacity": 0.9,
          },
        },
        beforeId
      );
    }
    // A distinct outer ring for records from a collection serving ahead of governance admission
    // (see `isProvisionalBotanicalCollection`) -- drawn on EVERY membership/spatial_class style
    // above rather than a fourth mutually-exclusive class, because "provisional" is an orthogonal
    // governance fact, not a competing claim about the coordinate or the determination. MapLibre
    // has no native dashed-circle-stroke; a ring one size up in a clearly non-taxonomic colour is
    // the plain-canvas equivalent, and it never obscures the base dot it surrounds.
    if (!m.getLayer(LAYER_ID_PROVISIONAL_RING)) {
      m.addLayer(
        {
          id: LAYER_ID_PROVISIONAL_RING,
          type: "circle",
          source: SOURCE_ID,
          filter: ["==", ["get", "provisional"], true],
          paint: {
            "circle-radius": PROVISIONAL_RING_RADIUS,
            "circle-color": "transparent",
            "circle-stroke-width": 1.5,
            "circle-stroke-color": BOTANICAL_OCCURRENCE_LEGEND[3].color,
            "circle-stroke-opacity": 0.85,
          },
        },
        beforeId
      );
    }
  }, []);

  const removeLayers = useCallback((m: MapLibreMap) => {
    safeRemoveLayerAndSource(
      m,
      [LAYER_ID_EXACT, LAYER_ID_GENERALIZED, LAYER_ID_POSSIBLE, LAYER_ID_PROVISIONAL_RING],
      SOURCE_ID
    );
  }, []);

  // The persistent `style.load` registration, keyed on `[map]` ALONE so it keeps its place in
  // the listener queue and -- critically -- is registered even while this layer is not drawable.
  // It used to live inside the draw effect below, AFTER that effect's
  // `zoom < BOTANICAL_DETAIL_MIN_ZOOM` early return, so it was absent for the whole time the map
  // sat below the detail floor and was re-registered (moving to the back of the queue) on every
  // zoom tick above it. Paired with it was a `once("style.load", ...)` fallback taken whenever
  // `isStyleLoaded()` read false -- and a `once` registered after that event has already fired
  // never runs, which is the documented way a custom-added layer silently never draws. Both
  // hazards are removed here; see `src/components/map/AGENTS.md`
  // "`isStyleLoaded()` is a signal to retry on, never a gate to drop writes behind".
  useEffect(() => {
    if (!map) return;
    const onStyleLoad = () => {
      const current = propsRef.current;
      if (!current.visible || !current.drawable || !current.geojson) return;
      if (current.zoom < BOTANICAL_DETAIL_MIN_ZOOM) return;
      addLayers(map);
    };
    map.on("style.load", onStyleLoad);
    return () => {
      map.off("style.load", onStyleLoad);
      removeLayers(map);
    };
  }, [map, addLayers, removeLayers]);

  // Parsed-style admission, not all-source `isStyleLoaded()` readiness: `getStyle()` returning a
  // style means `addSource`/`addLayer` are legal, while `isStyleLoaded()` also waits on unrelated
  // sources' tiles. See `src/components/map/AGENTS.md`. This effect deliberately has NO cleanup --
  // the previous version removed the layers on every data/zoom rerender and re-added them only
  // behind the global readiness gate, so any tick while readiness was pending left the layers
  // removed and never re-added. `addLayers` is idempotent and re-`setData`s an existing source, so
  // the drawable branch always ends installed AND current; an unparsed style waits for the
  // persistent `style.load` listener above, which reads the latest props off the ref.
  useEffect(() => {
    if (!map) return;
    if (!visible || !drawable || !geojson || zoom < BOTANICAL_DETAIL_MIN_ZOOM) {
      removeLayers(map);
    } else if (map.getStyle()) {
      addLayers(map);
    }
  }, [map, geojson, visible, drawable, zoom, addLayers, removeLayers]);

  // Delegated picking attaches ONCE per map. Keying it on data/zoom stacked a fresh pair of
  // layer-scoped click handlers on every draw cycle; a handler bound to a not-yet-created layer
  // never fires, and the query re-filters on `getLayer`, so binding early is safe.
  useEffect(() => {
    if (!map) return;
    const onClick = (event: MapMouseEvent) => {
      const features = map.queryRenderedFeatures(event.point, {
        layers: [LAYER_ID_EXACT, LAYER_ID_GENERALIZED, LAYER_ID_POSSIBLE].filter((id) => map.getLayer(id)),
      });
      const occurrenceId = features[0]?.properties?.occurrence_id;
      if (typeof occurrenceId === "string") {
        onSelectFeatureRef.current?.(occurrenceId);
      }
    };
    map.on("click", LAYER_ID_EXACT, onClick);
    map.on("click", LAYER_ID_GENERALIZED, onClick);
    map.on("click", LAYER_ID_POSSIBLE, onClick);

    return () => {
      map.off("click", LAYER_ID_EXACT, onClick);
      map.off("click", LAYER_ID_GENERALIZED, onClick);
      map.off("click", LAYER_ID_POSSIBLE, onClick);
    };
  }, [map]);

  return null;
}
