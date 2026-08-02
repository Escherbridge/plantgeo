"use client";

import { AlertTriangle, Globe2 } from "lucide-react";
import { trpc } from "@/lib/trpc/client";

function formatDegrees(value: number, positive: string, negative: string) {
  const hemisphere = value >= 0 ? positive : negative;
  return `${Math.abs(value).toFixed(1)}°${hemisphere}`;
}

/**
 * States plainly that data coverage is regional, not global.
 * The basemap is worldwide while every ingestion job is clamped to INGEST_BBOX,
 * so an empty layer outside that box means "not ingested here", not "nothing
 * happening here" -- a distinction the map itself cannot express.
 */
export function IngestionCoverageBadge() {
  const coverage = trpc.layers.getIngestionCoverage.useQuery(undefined, {
    staleTime: 60 * 60 * 1000,
    retry: false,
  });

  if (!coverage.data) return null;

  if (!coverage.data.configured) {
    return (
      <div className="pointer-events-none flex items-center gap-2 rounded-md border border-amber-400/60 bg-amber-50/95 px-2.5 py-1.5 text-xs font-medium text-amber-800 shadow-sm dark:border-amber-500/40 dark:bg-amber-950/80 dark:text-amber-300">
        <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
        <span>No ingestion region configured — data layers are empty</span>
      </div>
    );
  }

  const { west, south, east, north } = coverage.data.bbox;
  return (
    <div className="pointer-events-none flex items-center gap-2 rounded-md border border-[hsl(var(--border))] bg-[hsl(var(--background))]/95 px-2.5 py-1.5 text-xs font-medium text-[hsl(var(--muted-foreground))] shadow-sm">
      <Globe2 className="h-3.5 w-3.5 shrink-0" />
      <span>
        Data coverage: {formatDegrees(south, "N", "S")}–
        {formatDegrees(north, "N", "S")}, {formatDegrees(west, "E", "W")}–
        {formatDegrees(east, "E", "W")}
      </span>
    </div>
  );
}
