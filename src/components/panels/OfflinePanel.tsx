"use client";

import { useState, useEffect, useCallback } from "react";
import {
  calculateTileUrls,
  prefetchTiles,
  getStorageEstimateMB,
  clearTileCache,
  type TilePrefetchProgress,
  type BoundingBox,
} from "@/lib/offline/tile-cache";
import { useViewportBounds } from "@/hooks/useViewportProxiedLayers";
import { useOfflineSync } from "@/hooks/useOfflineSync";
import { Download, Trash2, AlertTriangle } from "lucide-react";

/**
 * The dock's Offline & Sync section body -- area download, storage usage and sync-conflict
 * resolution. Mounted only while that section is expanded (see `DockDetails.tsx`), so it owns
 * no close affordance and no positioning of its own: collapsing the section IS closing it, the
 * same contract every other details region follows. `SyncIndicator`, mounted separately as
 * floating status chrome in `MapView`, already states online/pending state, so this body does
 * not restate it -- see `src/components/map/AGENTS.md` "One manager, no floating surfaces".
 */
export function OfflinePanel() {
  const { conflicts, resolveConflict } = useOfflineSync();
  const { bbox: currentBbox } = useViewportBounds();

  const [minZoom, setMinZoom] = useState(5);
  const [maxZoom, setMaxZoom] = useState(12);
  const [isDownloading, setIsDownloading] = useState(false);
  const [progress, setProgress] = useState<TilePrefetchProgress | null>(null);
  const [storage, setStorage] = useState<{ usageMB: number; quotaMB: number }>({
    usageMB: 0,
    quotaMB: 0,
  });

  const refreshStorage = useCallback(async () => {
    const est = await getStorageEstimateMB();
    setStorage(est);
  }, []);

  useEffect(() => {
    refreshStorage();
  }, [refreshStorage]);

  const handleResolveConflict = async (id: string, resolution: "keep-local" | "discard-local") => {
    await resolveConflict(id, resolution);
  };

  const parseBbox = (): BoundingBox => {
    if (!currentBbox) {
      return { west: -125, south: 45, east: -116, north: 49 }; // PNW default
    }
    const [w, s, e, n] = currentBbox.split(",").map(Number);
    return { west: w || -125, south: s || 45, east: e || -116, north: n || 49 };
  };

  const handleDownload = async () => {
    setIsDownloading(true);
    const bounds = parseBbox();
    const urls = calculateTileUrls(bounds, minZoom, maxZoom);

    await prefetchTiles(urls, (prog) => {
      setProgress(prog);
      if (prog.isComplete) {
        setIsDownloading(false);
        refreshStorage();
      }
    });
  };

  const handleClearCache = async () => {
    await clearTileCache();
    await refreshStorage();
    setProgress(null);
  };

  return (
    <div className="space-y-4 text-xs text-[hsl(var(--foreground))]">
      {/* Storage Estimate */}
      <div className="flex items-center justify-between rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-3">
        <div>
          <p className="text-[hsl(var(--muted-foreground))]">Cache Storage Used</p>
          <p className="text-lg font-bold text-emerald-500">{storage.usageMB} MB</p>
        </div>
        <button
          onClick={handleClearCache}
          className="flex items-center gap-1 rounded-md border border-rose-800/40 bg-rose-950/40 px-3 py-1.5 text-rose-400 transition-colors hover:bg-rose-900/60 hover:text-rose-300"
        >
          <Trash2 className="h-3.5 w-3.5" />
          <span>Clear Cache</span>
        </button>
      </div>

      {/* Sync Conflicts -- an op whose local snapshot no longer matches the server, held
          here instead of blindly overwriting or silently retrying. */}
      {conflicts.length > 0 && (
        <div className="space-y-2">
          <h3 className="flex items-center gap-2 font-medium">
            <AlertTriangle className="h-4 w-4 text-rose-400" />
            <span>Sync Conflicts ({conflicts.length})</span>
          </h3>
          <div className="space-y-2">
            {conflicts.map((conflict) => (
              <div
                key={conflict.id}
                className="space-y-2 rounded-lg border border-rose-800/40 bg-rose-950/30 p-3 text-rose-200"
              >
                <div className="flex items-center justify-between">
                  <span className="font-medium capitalize">
                    {conflict.type} · {conflict.resource}
                  </span>
                  <span className="text-rose-400/70">
                    {conflict.conflictReason === "server-409" ? "Server rejected" : "Newer on server"}
                  </span>
                </div>
                <div className="flex gap-2">
                  <button
                    onClick={() => handleResolveConflict(conflict.id, "keep-local")}
                    className="flex-1 rounded bg-emerald-700/60 px-2 py-1 font-medium text-emerald-100 transition-colors hover:bg-emerald-600/70"
                  >
                    Keep Local
                  </button>
                  <button
                    onClick={() => handleResolveConflict(conflict.id, "discard-local")}
                    className="flex-1 rounded bg-slate-700/60 px-2 py-1 font-medium text-slate-200 transition-colors hover:bg-slate-600/70"
                  >
                    Discard Local
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Area Download Control */}
      <div className="space-y-3">
        <h3 className="font-medium">Download Viewport Tiles</h3>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="mb-1 block text-[hsl(var(--muted-foreground))]">
              Min Zoom Level ({minZoom})
            </label>
            <input
              type="range"
              min="3"
              max="10"
              value={minZoom}
              onChange={(e) => setMinZoom(Number(e.target.value))}
              className="w-full accent-emerald-500"
            />
          </div>
          <div>
            <label className="mb-1 block text-[hsl(var(--muted-foreground))]">
              Max Zoom Level ({maxZoom})
            </label>
            <input
              type="range"
              min="10"
              max="15"
              value={maxZoom}
              onChange={(e) => setMaxZoom(Number(e.target.value))}
              className="w-full accent-emerald-500"
            />
          </div>
        </div>

        <button
          onClick={handleDownload}
          disabled={isDownloading}
          className="flex w-full items-center justify-center gap-2 rounded-lg bg-emerald-600 px-4 py-2.5 font-medium text-white shadow-md transition-colors hover:bg-emerald-500 disabled:bg-[hsl(var(--muted))] disabled:text-[hsl(var(--muted-foreground))]"
        >
          <Download className="h-4 w-4" />
          <span>{isDownloading ? "Downloading Tiles..." : "Download Selected Region"}</span>
        </button>

        {progress && (
          <div className="space-y-1.5 pt-2">
            <div className="flex justify-between text-[hsl(var(--muted-foreground))]">
              <span>Downloading tiles</span>
              <span>
                {progress.percentage}% ({progress.completed}/{progress.total})
              </span>
            </div>
            <div className="h-2 w-full overflow-hidden rounded-full bg-[hsl(var(--muted))]">
              <div
                className="h-2 rounded-full bg-emerald-500 transition-all duration-200"
                style={{ width: `${progress.percentage}%` }}
              />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
