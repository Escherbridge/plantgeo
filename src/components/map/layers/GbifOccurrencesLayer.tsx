"use client";

/**
 * The GBIF specimen/observation layer -- a NEW component, not a mode of
 * `BotanicalOccurrencesLayer`, per the mission's own recommendation: a toggle needs to be
 * independently on/off, and that component's `LAYER_ID_*`/`SOURCE_ID` constants are not
 * source-parameterized (they are plain module-level strings, not built from a `collection_key`
 * argument). Parameterizing them would mean every existing UBC id becomes a template result
 * instead of a stable string, for the benefit of exactly one other caller. A sibling component
 * with its own ids is the smaller, safer diff, and it is what this file is.
 *
 * What IS shared: the structural pattern this file copies wholesale from
 * `BotanicalOccurrencesLayer.tsx` (parsed-style admission, idempotent `addLayers` guarded by
 * `getSource`/`getLayer`, a persistent `style.load` listener keyed on `[map]` alone so it never
 * falls out of the listener queue while below the detail floor -- see that file's own comment
 * and `src/components/map/AGENTS.md` "`isStyleLoaded()` is a signal to retry on, never a gate to
 * drop writes behind") -- and the GeoJSON conversion itself: the caller (`LayerManager`) builds
 * this layer's `geojson` prop with the SAME `botanicalOccurrencesToGeoJSON` helper UBC uses,
 * called on the GBIF-only slice of the one shared botanical-occurrences response. There is no
 * second query and no second wire shape here, only a second set of map layers over a filtered
 * slice of data everyone already fetched once.
 *
 * GBIF has no `possible`-membership concept the way UBC's determination workflow does (every
 * GBIF row here maps to a `confirmed` membership at ingest -- see Lane 1/2's normalize.py
 * mapping), so this component draws only the two `confirmed` classes plus the provisional ring;
 * a third "possible" style is omitted rather than kept dead, and can be added back the moment a
 * real GBIF row needs it.
 */

import { useEffect, useRef, useCallback } from "react";
import type { Map as MapLibreMap, GeoJSONSource, MapMouseEvent } from "maplibre-gl";
import { getFirstSymbolLayer, safeRemoveLayerAndSource } from "@/lib/map/layer-utils";
import { BOTANICAL_DETAIL_MIN_ZOOM } from "@/lib/botanical-occurrences";

const SOURCE_ID = "gbif-occurrences";
const LAYER_ID_EXACT = "gbif-occurrences-exact";
const LAYER_ID_GENERALIZED = "gbif-occurrences-generalized";
const LAYER_ID_PROVISIONAL_RING = "gbif-occurrences-provisional-ring";

/**
 * Circle-radius/style legend for this layer. A distinct palette from
 * `BOTANICAL_OCCURRENCE_LEGEND` (blue family rather than green) so a reader can tell the two
 * sources' dots apart on sight when both toggles are on at once, without having to hover every
 * point to read `collection_key` off the tooltip.
 */
export const GBIF_OCCURRENCE_LEGEND = [
  { label: "GBIF, exact locality", color: "#4a8fe0" },
  { label: "GBIF, generalized locality", color: "#2a5a9c" },
  { label: "Provisional: not yet governance-admitted", color: "#e8813f" },
] as const;

/** Circle-radius of the outer provisional ring; matches the UBC layer's sizing. */
const PROVISIONAL_RING_RADIUS = 9;

interface GbifOccurrencesLayerProps {
  map: MapLibreMap | null;
  geojson: GeoJSON.FeatureCollection | null;
  zoom: number;
  visible?: boolean;
  onSelectFeature?: (occurrenceId: string) => void;
}

/**
 * Detail-zoom GBIF occurrence points. Mirrors `BotanicalOccurrencesLayer`'s own zoom-band
 * contract: only mounted/drawable at `zoom >= BOTANICAL_DETAIL_MIN_ZOOM`, decided by the caller,
 * and this component re-checks the floor itself as belt-and-braces.
 */
export function GbifOccurrencesLayer({
  map,
  geojson,
  zoom,
  visible = true,
  onSelectFeature,
}: GbifOccurrencesLayerProps) {
  const propsRef = useRef({ geojson, visible, zoom });
  propsRef.current = { geojson, visible, zoom };
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
            "circle-color": GBIF_OCCURRENCE_LEGEND[0].color,
            "circle-stroke-width": 1,
            "circle-stroke-color": "#0a1f2f",
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
            "circle-color": GBIF_OCCURRENCE_LEGEND[1].color,
            "circle-opacity": 0.45,
            "circle-stroke-width": 1,
            "circle-stroke-color": "#0a1f2f",
          },
        },
        beforeId
      );
    }
    // Same orthogonal-governance-fact ring as `BotanicalOccurrencesLayer` -- see that file's
    // comment on `LAYER_ID_PROVISIONAL_RING` for why it is a ring rather than a fourth class.
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
            "circle-stroke-color": GBIF_OCCURRENCE_LEGEND[2].color,
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
      [LAYER_ID_EXACT, LAYER_ID_GENERALIZED, LAYER_ID_PROVISIONAL_RING],
      SOURCE_ID
    );
  }, []);

  // See `BotanicalOccurrencesLayer`'s identical block for why this listener is keyed on `[map]`
  // alone and registered unconditionally rather than gated behind the zoom/visibility check --
  // the check happens INSIDE the handler, not in the effect's dependencies. Its cleanup owns the
  // teardown for the whole component, so it runs only on unmount or map replacement, never on a
  // data/zoom tick.
  useEffect(() => {
    if (!map) return;
    const onStyleLoad = () => {
      const current = propsRef.current;
      if (!current.visible || !current.geojson || current.zoom < BOTANICAL_DETAIL_MIN_ZOOM) return;
      addLayers(map);
    };
    map.on("style.load", onStyleLoad);
    return () => {
      map.off("style.load", onStyleLoad);
      removeLayers(map);
    };
  }, [map, addLayers, removeLayers]);

  // Parsed style admits sources before unrelated tiles finish; see map/AGENTS.md. This effect has
  // NO cleanup on purpose: it used to remove the layers on every data/zoom rerender and then
  // re-add only if a global `isStyleLoaded()` read true, so a tick while readiness was pending
  // left the layer removed-and-not-re-added. Now a rerender against a parsed style always ends
  // installed and current (`addLayers` is idempotent and re-`setData`s an existing source), and a
  // genuinely unparsed style simply waits for the persistent `style.load` listener above.
  useEffect(() => {
    if (!map) return;
    if (!visible || !geojson || zoom < BOTANICAL_DETAIL_MIN_ZOOM) {
      removeLayers(map);
    } else if (map.getStyle()) {
      addLayers(map);
    }
  }, [map, geojson, visible, zoom, addLayers, removeLayers]);

  // Delegated picking attaches ONCE per map, not once per draw cycle: layer-scoped handlers keyed
  // on changing data/zoom would stack duplicates every tick. A handler bound to a layer that does
  // not exist yet simply never fires, and the query re-filters on `getLayer` anyway.
  useEffect(() => {
    if (!map) return;
    const onClick = (event: MapMouseEvent) => {
      const features = map.queryRenderedFeatures(event.point, {
        layers: [LAYER_ID_EXACT, LAYER_ID_GENERALIZED].filter((id) => map.getLayer(id)),
      });
      const occurrenceId = features[0]?.properties?.occurrence_id;
      if (typeof occurrenceId === "string") {
        onSelectFeatureRef.current?.(occurrenceId);
      }
    };
    map.on("click", LAYER_ID_EXACT, onClick);
    map.on("click", LAYER_ID_GENERALIZED, onClick);

    return () => {
      map.off("click", LAYER_ID_EXACT, onClick);
      map.off("click", LAYER_ID_GENERALIZED, onClick);
    };
  }, [map]);

  return null;
}
