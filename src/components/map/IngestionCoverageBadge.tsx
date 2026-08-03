"use client";

import { AlertTriangle, Globe2 } from "lucide-react";
import { trpc } from "@/lib/trpc/client";
import {
  describeCoverageRegion,
  formatCoverageBounds,
} from "@/lib/map/coverage-region";

/**
 * States plainly that data coverage is regional, not global.
 * The basemap is worldwide while every ingestion job is clamped to INGEST_BBOX,
 * so an empty layer outside that box means "not ingested here", not "nothing
 * happening here" -- a distinction the map itself cannot express.
 *
 * Renders as an inline strip, hosted inside the search panel (see SearchBar),
 * so it never overlaps the map toolbar.
 */
export function IngestionCoverageBadge() {
  const coverage = trpc.layers.getIngestionCoverage.useQuery(undefined, {
    staleTime: 60 * 60 * 1000,
    retry: false,
  });

  if (!coverage.data) return null;

  if (!coverage.data.configured) {
    return (
      <div className="flex items-center gap-2 border-t border-[hsl(var(--border))] px-3 py-1.5 text-xs font-medium text-amber-600 dark:text-amber-400">
        <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
        <span>No ingestion region configured</span>
      </div>
    );
  }

  const { bbox } = coverage.data;
  return (
    <div
      className="flex items-center gap-2 border-t border-[hsl(var(--border))] px-3 py-1.5 text-xs text-[hsl(var(--muted-foreground))]"
      title={`Data coverage: ${formatCoverageBounds(bbox)}`}
    >
      <Globe2 className="h-3.5 w-3.5 shrink-0" />
      <span className="truncate">
        Covering {describeCoverageRegion(bbox)}
      </span>
    </div>
  );
}
