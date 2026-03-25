import type { Map as MapLibreMap } from "maplibre-gl";

/**
 * Returns the ID of the first symbol layer in the current map style.
 * Used as a `beforeId` when adding overlay layers so they render beneath labels.
 *
 * Returns `undefined` if no symbol layer exists (e.g., during style transitions
 * or on styles without labels), which causes `map.addLayer` to add the layer
 * at the top of the stack -- a safe fallback.
 */
export function getFirstSymbolLayer(map: MapLibreMap): string | undefined {
  const style = map.getStyle();
  if (!style?.layers) return undefined;
  for (const layer of style.layers) {
    if (layer.type === "symbol") return layer.id;
  }
  return undefined;
}

/**
 * Safely removes a layer and its source from the map if they exist.
 * Guards against style-not-loaded and missing layer/source errors.
 */
export function safeRemoveLayerAndSource(
  map: MapLibreMap,
  layerIds: string[],
  sourceId: string
): void {
  try {
    if (!map.getStyle()) return;
    for (const layerId of layerIds) {
      if (map.getLayer(layerId)) map.removeLayer(layerId);
    }
    if (map.getSource(sourceId)) map.removeSource(sourceId);
  } catch {
    // Style may have been removed between checks -- safe to ignore
  }
}
