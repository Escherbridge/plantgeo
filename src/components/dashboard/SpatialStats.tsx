"use client";

import { trpc } from "@/lib/trpc/client";
import { BarChart } from "./BarChart";
import { Skeleton } from "@/components/ui/skeleton";

export function SpatialStats() {
  const { data: layerStats, isLoading } = trpc.analytics.layerStats.useQuery();
  const { data: systemStats, isLoading: systemLoading } = trpc.analytics.systemStats.useQuery();

  if (isLoading || systemLoading) {
    return (
      <div className="flex flex-col gap-3">
        <div className="grid grid-cols-3 gap-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-14 rounded-lg" />
          ))}
        </div>
        <Skeleton className="h-40 rounded-lg" />
      </div>
    );
  }

  const barData = (layerStats ?? []).map((s) => ({
    label: s.layerName,
    value: Number(s.count),
  }));

  return (
    <div className="flex flex-col gap-4">
      {/* System overview */}
      <div className="grid grid-cols-3 gap-3">
        <div className="rounded-lg bg-[hsl(var(--muted))] px-3 py-2 flex flex-col gap-0.5">
          <span className="text-xs text-[hsl(var(--muted-foreground))]">Layers</span>
          <span className="text-xl font-bold text-[hsl(var(--foreground))]">
            {systemStats?.layerCount ?? 0}
          </span>
        </div>
        <div className="rounded-lg bg-[hsl(var(--muted))] px-3 py-2 flex flex-col gap-0.5">
          <span className="text-xs text-[hsl(var(--muted-foreground))]">Features</span>
          <span className="text-xl font-bold text-[hsl(var(--foreground))]">
            {(systemStats?.featureCount ?? 0).toLocaleString()}
          </span>
        </div>
        <div className="rounded-lg bg-[hsl(var(--muted))] px-3 py-2 flex flex-col gap-0.5">
          <span className="text-xs text-[hsl(var(--muted-foreground))]">Streams</span>
          <span className="text-xl font-bold text-[hsl(var(--foreground))]">
            {systemStats?.activeStreams ?? 0}
          </span>
        </div>
      </div>

      {/* Feature counts per layer */}
      {barData.length > 0 ? (
        <div>
          <p className="text-xs text-[hsl(var(--muted-foreground))] mb-2 font-medium">
            Features per Layer
          </p>
          <BarChart data={barData} horizontal />
        </div>
      ) : (
        <p className="text-sm text-[hsl(var(--muted-foreground))] text-center py-4">
          No layer data yet. Add layers on the main map to see statistics.
        </p>
      )}
    </div>
  );
}
