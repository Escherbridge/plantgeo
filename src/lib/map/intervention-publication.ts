import type { Map as MapLibreMap, VectorTileSource } from "maplibre-gl";
import { refreshDynamicTiles } from "@/lib/offline/tile-cache";

export const INTERVENTION_PUBLICATION_KEY = "plantgeo:intervention-publication";
const PUBLICATION_EVENT = "plantgeo:intervention-published";
let latestRevision = "";

export function readInterventionPublicationRevision(): string {
  if (typeof window === "undefined") return latestRevision;
  try {
    return window.localStorage.getItem(INTERVENTION_PUBLICATION_KEY) ?? latestRevision;
  } catch {
    return latestRevision;
  }
}

/** Purge saved tiles before notifying current and other map tabs. */
export async function notifyInterventionPublication(): Promise<void> {
  if (typeof window === "undefined") return;
  try {
    await refreshDynamicTiles("intervention_tiles");
  } catch {
    // The revision also bypasses stale worker and HTTP cache entries.
  }
  latestRevision = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  try {
    window.localStorage.setItem(INTERVENTION_PUBLICATION_KEY, latestRevision);
  } catch {
    // Same-tab updates remain available when browser storage is disabled.
  }
  window.dispatchEvent(new Event(PUBLICATION_EVENT));
}

export function subscribeInterventionPublication(onPublication: () => void): () => void {
  const onStorage = (event: StorageEvent) => {
    if (event.key === INTERVENTION_PUBLICATION_KEY) onPublication();
  };
  window.addEventListener(PUBLICATION_EVENT, onPublication);
  window.addEventListener("storage", onStorage);
  window.addEventListener("focus", onPublication);
  return () => {
    window.removeEventListener(PUBLICATION_EVENT, onPublication);
    window.removeEventListener("storage", onStorage);
    window.removeEventListener("focus", onPublication);
  };
}

/** Reload only the intervention source, including after a basemap style replacement. */
export function refreshPublishedInterventionSource(map: MapLibreMap, revision: string): void {
  if (!revision) return;
  const source = map.getSource("intervention_tiles") as VectorTileSource | undefined;
  if (!source || typeof source.setTiles !== "function" || !source.tiles?.length) return;
  const tiles = source.tiles.map((template) => {
    const [path, query = ""] = template.split("?");
    const params = new URLSearchParams(query);
    params.set("publication", revision);
    return `${path}?${params.toString()}`;
  });
  if (tiles.some((tile, index) => tile !== source.tiles[index])) source.setTiles(tiles);
}
