"use client";

import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Flame, Wind, AlertTriangle, MapPin } from "lucide-react";
import { LayerToggle } from "@/components/ui/layer-toggle";
import { useFireData } from "@/hooks/useFireData";

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

interface FireDashboardProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Map center reserved for future published weather observations. */
  centerLat?: number;
  centerLon?: number;
}

export function FireDashboard({
  open,
  onOpenChange,
}: FireDashboardProps) {
  const fireData = useFireData(open);
  const effectiveFireCount = fireData.count;

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" onOpenChange={onOpenChange}>
        <SheetHeader>
          <SheetTitle className="flex items-center gap-2">
            <Flame className="h-5 w-5 text-red-500" />
            Fire Dashboard
          </SheetTitle>
        </SheetHeader>

        <LayerToggle layerId="fire" label="Fire Detections" />
        <LayerToggle layerId="fire-perimeters" label="Fire Risk Zones" />
        <LayerToggle layerId="sensors" label="Sensor Network" />

        <div className="flex flex-col gap-4 mt-4 overflow-y-auto max-h-[calc(100vh-8rem)]">
          {fireData.error && !fireData.isLoading && (
            <div className="flex items-center gap-2 rounded-lg border border-amber-300 bg-amber-50 dark:bg-amber-950/20 px-3 py-2">
              <AlertTriangle className="h-4 w-4 text-amber-600 shrink-0" />
              <p className="text-xs text-amber-700 dark:text-amber-400 font-medium">
                Published fire detections could not be refreshed. Previously loaded data may be stale.
              </p>
            </div>
          )}

          <div className="flex items-center gap-2 rounded-lg border border-amber-300 bg-amber-50 dark:bg-amber-950/20 px-3 py-2">
            <AlertTriangle aria-hidden="true" className="h-4 w-4 text-amber-600 shrink-0" />
            <p className="text-xs text-amber-700 dark:text-amber-400 font-medium">
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
      </SheetContent>
    </Sheet>
  );
}
