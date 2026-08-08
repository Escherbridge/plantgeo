"use client";

import { Flame, Wind, AlertTriangle, MapPin } from "lucide-react";
import { useFireData } from "@/hooks/useFireData";
import { useDebouncedMapDay } from "@/lib/map/layer-toggle-context";

interface StatCardProps {
  icon: React.ReactNode;
  label: string;
  value: string | number;
  sub?: string;
  highlight?: boolean;
}

function StatCard({ icon, label, value, sub, highlight }: StatCardProps) {
  return (
    <div
      className={`rounded-lg border p-3 flex flex-col gap-1 ${
        highlight
          ? "border-red-300 bg-red-50 dark:bg-red-950/20"
          : "border-[hsl(var(--border))] bg-[hsl(var(--card))]"
      }`}
    >
      <div className="flex items-center gap-2 text-[hsl(var(--muted-foreground))]">
        {icon}
        <span className="text-xs font-medium">{label}</span>
      </div>
      <span
        className={`text-2xl font-bold ${highlight ? "text-red-600" : "text-[hsl(var(--foreground))]"}`}
      >
        {value}
      </span>
      {sub && (
        <span className="text-xs text-[hsl(var(--muted-foreground))]">{sub}</span>
      )}
    </div>
  );
}

/**
 * What the fire layers are showing, as the Fire section of the map dock.
 *
 * Mounted only while that section is expanded -- which is what `useFireData`'s enabled flag
 * now reads, in place of the `open` prop the sheet passed it. The four `<LayerToggle>` rows
 * this panel used to carry are gone: the section's own layer rows are the switches now, and
 * two controls over one `activeLayers` entry, one of them out of sight, is the drift the dock
 * was built to end.
 */
export function FireDetails() {
  // The same settled day the map's own fire layer reads, so the count here can never describe
  // a different date than the pins beside it -- one query key, one answer.
  const { requestDate } = useDebouncedMapDay();
  const fireData = useFireData(true, requestDate);
  const effectiveFireCount = fireData.count;

  return (
    <div className="flex flex-col gap-4">
      {fireData.error && !fireData.isLoading && (
        <div className="flex items-center gap-2 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 dark:bg-amber-950/20">
          <AlertTriangle className="h-4 w-4 shrink-0 text-amber-600" />
          <p className="text-xs font-medium text-amber-700 dark:text-amber-400">
            Published fire detections could not be refreshed. Previously loaded data may be stale.
          </p>
        </div>
      )}

      <div className="flex items-center gap-2 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 dark:bg-amber-950/20">
        <AlertTriangle aria-hidden="true" className="h-4 w-4 shrink-0 text-amber-600" />
        <p className="text-xs font-medium text-amber-700 dark:text-amber-400">
          Published weather observations are unavailable for this location. No fallback reading is shown.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-2">
        <StatCard
          icon={<Flame className="h-3.5 w-3.5" />}
          label="Fire Detections"
          value={fireData.isLoading ? "..." : effectiveFireCount}
          sub="Published FIRMS detections, last 24h"
        />
        <StatCard
          icon={<Wind className="h-3.5 w-3.5" />}
          label="Wind Speed"
          value="N/A"
          sub="No published observation"
        />
        <StatCard
          icon={<MapPin className="h-3.5 w-3.5" />}
          label="Humidity"
          value="N/A"
          sub="No published observation"
        />
        <StatCard
          icon={<Flame className="h-3.5 w-3.5" />}
          label="Temperature"
          value="N/A"
          sub="No published observation"
        />
      </div>

      <div className="rounded-lg border border-dashed border-[hsl(var(--border))] p-3 text-xs text-[hsl(var(--muted-foreground))]">
        Seven-day fire history will appear after a versioned warehouse aggregate is published.
      </div>
    </div>
  );
}
