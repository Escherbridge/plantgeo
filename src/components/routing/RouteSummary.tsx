"use client";

import { Clock, Route, AlertTriangle } from "lucide-react";
import { useRoutingStore } from "@/stores/routing-store";
import type { DecodedRoute } from "@/lib/server/services/routing";
import { cn } from "@/lib/utils";

function formatDuration(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (h > 0) return `${h}h ${m}m`;
  return `${m} min`;
}

function formatDistance(km: number): string {
  if (km < 1) return `${Math.round(km * 1000)} m`;
  return `${km.toFixed(1)} km`;
}

interface RouteSummaryProps {
  route: DecodedRoute;
  alternatives: DecodedRoute[];
}

export function RouteSummary({ route, alternatives }: RouteSummaryProps) {
  const { selectAlternative } = useRoutingStore();

  return (
    <div className="flex flex-col gap-2">
      <div className="rounded-md bg-emerald-500/10 border border-emerald-500/20 p-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 text-sm font-semibold text-emerald-600 dark:text-emerald-400">
            <Clock className="h-4 w-4" />
            {formatDuration(route.summary.time)}
          </div>
          <div className="flex items-center gap-2 text-sm text-[hsl(var(--muted-foreground))]">
            <Route className="h-4 w-4" />
            {formatDistance(route.summary.length)}
          </div>
        </div>
        {(route.summary.hasToll || route.summary.hasHighway) && (
          <div className="mt-1 flex items-center gap-1 text-xs text-amber-500">
            <AlertTriangle className="h-3 w-3" />
            {[route.summary.hasToll && "Tolls", route.summary.hasHighway && "Highway"]
              .filter(Boolean)
              .join(" · ")}
          </div>
        )}
      </div>

      {alternatives.length > 0 && (
        <div className="flex flex-col gap-1">
          <p className="text-xs text-[hsl(var(--muted-foreground))]">Alternatives</p>
          {alternatives.slice(0, 3).map((alt, i) => (
            <button
              key={i}
              type="button"
              onClick={() => selectAlternative(i)}
              className={cn(
                "flex items-center justify-between rounded-md border border-[hsl(var(--border))] px-3 py-2 text-sm hover:bg-[hsl(var(--accent))] hover:text-[hsl(var(--accent-foreground))] transition-colors"
              )}
            >
              <span className="flex items-center gap-2 text-[hsl(var(--muted-foreground))]">
                <Clock className="h-3.5 w-3.5" />
                {formatDuration(alt.summary.time)}
              </span>
              <span className="text-xs text-[hsl(var(--muted-foreground))]">
                {formatDistance(alt.summary.length)}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
