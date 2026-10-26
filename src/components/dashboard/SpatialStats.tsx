"use client";

import { trpc } from "@/lib/trpc/client";
import { BarChart } from "./BarChart";
import { Skeleton } from "@/components/ui/skeleton";
import { TOUCH_TARGET } from "@/components/ui/touch-target";

/** The raw per-layer rows `featureCounts` answers with, written out as one CSV line each. */
function downloadFeatureCountsCsv(rows: { layerId: string; count: number }[]): void {
  if (rows.length === 0) return;
  const lines = ["layer_id,count", ...rows.map((row) => `${row.layerId},${row.count}`)];
  const blob = new Blob([lines.join("\n")], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = "feature_counts.csv";
  anchor.click();
  URL.revokeObjectURL(url);
}

/**
 * `featureCounts` and `layerStats` run the identical join over `geo.layers`/`geo.features`;
 * `layerStats` only adds the layer's name, which `BarChart` needs for its axis labels and
 * `featureCounts` does not carry. So the panel draws from `layerStats` and surfaces
 * `featureCounts` instead as the raw CSV export for that same chart -- a real, distinct use of
 * the procedure rather than a second chart drawing the same bars twice.
 */
export function SpatialStats() {
  const { data: layerStats, isLoading } = trpc.analytics.layerStats.useQuery();
  const { data: systemStats, isLoading: systemLoading } = trpc.analytics.systemStats.useQuery();
  const { data: featureCounts } = trpc.analytics.featureCounts.useQuery();
  const { data: recentActivity, isLoading: recentActivityLoading } =
    trpc.analytics.recentActivity.useQuery({ hours: 24 });

  if (isLoading || systemLoading) {
    return (
      <div className="flex flex-col gap-3">
        {/* Renders inside the /dashboard route at full viewport width (not the 19rem map
            dock), so a phone genuinely has less room here -- collapse to one column. */}
        <div className="grid grid-cols-3 max-sm:grid-cols-1 gap-3">
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
      {/* Same /dashboard-width rationale as the skeleton above. */}
      <div className="grid grid-cols-3 max-sm:grid-cols-1 gap-3">
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
            {systemStats?.activeStreams ?? "—"}
          </span>
        </div>
      </div>

      {/* Feature counts per layer */}
      {barData.length > 0 ? (
        <div>
          <div className="mb-2 flex items-center justify-between gap-2">
            <p className="text-xs text-[hsl(var(--muted-foreground))] font-medium">
              Features per Layer
            </p>
            {featureCounts && featureCounts.length > 0 && (
              <button
                type="button"
                onClick={() => downloadFeatureCountsCsv(featureCounts)}
                className={`text-xs text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--foreground))] transition-colors px-2 py-0.5 rounded border border-[hsl(var(--border))] hover:border-[hsl(var(--foreground))] ${TOUCH_TARGET}`}
              >
                CSV
              </button>
            )}
          </div>
          <BarChart data={barData} horizontal />
        </div>
      ) : (
        <p className="text-sm text-[hsl(var(--muted-foreground))] text-center py-4">
          No layer data yet. Add layers on the main map to see statistics.
        </p>
      )}

      {/* New features in the last 24 hours, same join as "Features per Layer" with a creation
          window added -- a layer with no publication activity simply has no bar, which is the
          honest answer rather than a zero rendered over a blank chart. */}
      <div>
        <p className="text-xs text-[hsl(var(--muted-foreground))] mb-2 font-medium">
          New Features (24h)
        </p>
        {recentActivityLoading ? (
          <Skeleton className="h-24 rounded-lg" />
        ) : (recentActivity ?? []).length > 0 ? (
          <BarChart
            data={(recentActivity ?? []).map((row) => ({
              label: row.layerId,
              value: Number(row.count),
            }))}
            horizontal
            color="#6366f1"
          />
        ) : (
          <p className="text-sm text-[hsl(var(--muted-foreground))] text-center py-4">
            No new features published in the last 24 hours.
          </p>
        )}
      </div>
    </div>
  );
}
