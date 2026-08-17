/**
 * Offline Tile Cache strategy helper.
 * Provides bounding-box tile coordinate calculation, Service Worker tile pre-fetching,
 * storage usage estimation, and cache clearing.
 */

import { createBudgetedFetch } from "@/lib/net/request-budget";

/** Only the no-service-worker fallback below needs this -- the SW-mediated path is already
 *  serial by accident of its message-passing design, not by intent; see
 *  src/lib/net/AGENTS.md "The problem this replaces". */
const budgetedFetch = createBudgetedFetch("tile-prefetch");

export interface BoundingBox {
  west: number;
  south: number;
  east: number;
  north: number;
}

export interface TilePrefetchProgress {
  completed: number;
  total: number;
  percentage: number;
  isComplete: boolean;
}

export function lon2tile(lon: number, zoom: number): number {
  return Math.floor(((lon + 180) / 360) * Math.pow(2, zoom));
}

export function lat2tile(lat: number, zoom: number): number {
  const rad = (lat * Math.PI) / 180;
  return Math.floor(
    ((1 - Math.log(Math.tan(rad) + 1 / Math.cos(rad)) / Math.PI) / 2) * Math.pow(2, zoom)
  );
}

const TILE_TEMPLATES = [
  "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
];

/**
 * Calculates all tile URLs for a given bounding box and zoom range.
 */
export function calculateTileUrls(
  bbox: BoundingBox,
  minZoom = 5,
  maxZoom = 12,
  maxTileLimit = 500
): string[] {
  const urls: string[] = [];

  for (let z = minZoom; z <= maxZoom; z++) {
    const minX = lon2tile(bbox.west, z);
    const maxX = lon2tile(bbox.east, z);
    const minY = lat2tile(bbox.north, z);
    const maxY = lat2tile(bbox.south, z);

    for (let x = Math.min(minX, maxX); x <= Math.max(minX, maxX); x++) {
      for (let y = Math.min(minY, maxY); y <= Math.max(minY, maxY); y++) {
        for (const template of TILE_TEMPLATES) {
          const url = template
            .replace("{z}", String(z))
            .replace("{x}", String(x))
            .replace("{y}", String(y));
          urls.push(url);
          if (urls.length >= maxTileLimit) {
            return urls;
          }
        }
      }
    }
  }

  return urls;
}

/**
 * Dispatches tile prefetch request to Service Worker.
 */
export function prefetchTiles(
  urls: string[],
  onProgress?: (progress: TilePrefetchProgress) => void
): Promise<void> {
  return new Promise((resolve) => {
    if (typeof navigator === "undefined" || !navigator.serviceWorker || !navigator.serviceWorker.controller) {
      // Fallback: fetch directly
      let completed = 0;
      const total = urls.length;
      if (total === 0) {
        onProgress?.({ completed: 0, total: 0, percentage: 100, isComplete: true });
        resolve();
        return;
      }
      for (const url of urls) {
        budgetedFetch(url, { mode: "cors" })
          .catch(() => {})
          .finally(() => {
            completed++;
            onProgress?.({
              completed,
              total,
              percentage: Math.round((completed / total) * 100),
              isComplete: completed >= total,
            });
            if (completed >= total) resolve();
          });
      }
      return;
    }

    const handler = (event: MessageEvent) => {
      if (!event.data || typeof event.data !== "object") return;
      if (event.data.type === "PREFETCH_PROGRESS") {
        onProgress?.({
          completed: event.data.completed,
          total: event.data.total,
          percentage: Math.round((event.data.completed / event.data.total) * 100),
          isComplete: false,
        });
      } else if (event.data.type === "PREFETCH_COMPLETE") {
        navigator.serviceWorker.removeEventListener("message", handler);
        onProgress?.({
          completed: event.data.total,
          total: event.data.total,
          percentage: 100,
          isComplete: true,
        });
        resolve();
      }
    };

    navigator.serviceWorker.addEventListener("message", handler);
    navigator.serviceWorker.controller.postMessage({
      type: "PREFETCH_TILES",
      urls,
    });
  });
}

/**
 * Returns estimated storage usage in MB.
 */
export async function getStorageEstimateMB(): Promise<{ usageMB: number; quotaMB: number }> {
  if (typeof navigator === "undefined" || !navigator.storage || !navigator.storage.estimate) {
    return { usageMB: 0, quotaMB: 0 };
  }
  try {
    const { usage = 0, quota = 0 } = await navigator.storage.estimate();
    return {
      usageMB: Math.round((usage / (1024 * 1024)) * 10) / 10,
      quotaMB: Math.round((quota / (1024 * 1024)) * 10) / 10,
    };
  } catch {
    return { usageMB: 0, quotaMB: 0 };
  }
}

/**
 * Clears tile cache.
 */
export async function clearTileCache(): Promise<void> {
  if (typeof caches === "undefined") return;
  const keys = await caches.keys();
  for (const key of keys) {
    if (key.startsWith("plantgeo-")) {
      await caches.delete(key);
    }
  }
}
