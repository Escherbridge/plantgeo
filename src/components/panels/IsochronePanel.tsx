"use client";

import { useState } from "react";
import { Timer } from "lucide-react";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";
import { trpc } from "@/lib/trpc/client";
import { WaypointInput } from "@/components/routing/WaypointInput";
import type { NormalizedGeocodingResult } from "@/lib/server/services/geocoding";

const TIME_INTERVALS = [5, 10, 15, 30, 60] as const;

const COSTING_OPTIONS = [
  { value: "auto", label: "Car" },
  { value: "bicycle", label: "Bike" },
  { value: "pedestrian", label: "Walk" },
] as const;

const CONTOUR_COLORS = ["#d1fae5", "#6ee7b7", "#34d399", "#10b981", "#059669"];

interface IsochronePanelProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onIsochroneData?: (data: unknown) => void;
}

export function IsochronePanel({ open, onOpenChange, onIsochroneData }: IsochronePanelProps) {
  const [originQuery, setOriginQuery] = useState("");
  const [origin, setOrigin] = useState<{ lat: number; lon: number } | null>(null);
  const [selectedIntervals, setSelectedIntervals] = useState<number[]>([10, 20, 30]);
  const [costing, setCosting] = useState<"auto" | "bicycle" | "pedestrian">("auto");

  const isochroneMutation = trpc.routing.isochrone.useMutation({
    onSuccess: (data) => {
      onIsochroneData?.(data);
    },
  });

  function handleOriginSelect(result: NormalizedGeocodingResult) {
    setOrigin({ lat: result.coordinates[1], lon: result.coordinates[0] });
    setOriginQuery(result.name);
  }

  function toggleInterval(interval: number) {
    setSelectedIntervals((prev) =>
      prev.includes(interval) ? prev.filter((i) => i !== interval) : [...prev, interval].sort((a, b) => a - b)
    );
  }

  function handleCalculate() {
    if (!origin || selectedIntervals.length === 0) return;
    isochroneMutation.mutate({
      locations: [origin],
      costing,
      contours: selectedIntervals.map((time, i) => ({
        time,
        color: CONTOUR_COLORS[i % CONTOUR_COLORS.length],
      })),
      polygons: true,
    });
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="left" onOpenChange={onOpenChange}>
        <SheetHeader>
          <SheetTitle className="flex items-center gap-2">
            <Timer className="h-5 w-5" />
            Isochrone Analysis
          </SheetTitle>
        </SheetHeader>

        <div className="flex flex-col gap-4 mt-4">
          <div>
            <label className="mb-1.5 block text-xs font-medium text-[hsl(var(--muted-foreground))]">
              Origin
            </label>
            <WaypointInput
              placeholder="Search for a location"
              value={originQuery}
              onChange={setOriginQuery}
              onSelect={handleOriginSelect}
              onClear={() => {
                setOrigin(null);
                setOriginQuery("");
              }}
            />
          </div>

          <div>
            <label className="mb-1.5 block text-xs font-medium text-[hsl(var(--muted-foreground))]">
              Travel Mode
            </label>
            <div className="flex gap-2">
              {COSTING_OPTIONS.map(({ value, label }) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => setCosting(value)}
                  className={`flex-1 rounded-md px-3 py-1.5 text-sm transition-colors ${
                    costing === value
                      ? "bg-emerald-500 text-white"
                      : "bg-[hsl(var(--muted))] text-[hsl(var(--muted-foreground))] hover:bg-[hsl(var(--accent))]"
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>

          <div>
            <label className="mb-1.5 block text-xs font-medium text-[hsl(var(--muted-foreground))]">
              Time Intervals (minutes)
            </label>
            <div className="flex flex-wrap gap-2">
              {TIME_INTERVALS.map((interval) => (
                <label key={interval} className="flex items-center gap-1.5 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={selectedIntervals.includes(interval)}
                    onChange={() => toggleInterval(interval)}
                    className="accent-emerald-500"
                  />
                  <span className="text-sm">{interval} min</span>
                </label>
              ))}
            </div>
          </div>

          {isochroneMutation.isError && (
            <p className="text-sm text-[hsl(var(--destructive))]">
              Failed to calculate isochrone. Please try again.
            </p>
          )}

          <Button
            className="bg-emerald-500 hover:bg-emerald-600 text-white"
            disabled={!origin || selectedIntervals.length === 0 || isochroneMutation.isPending}
            onClick={handleCalculate}
          >
            {isochroneMutation.isPending ? "Calculating..." : "Calculate Isochrone"}
          </Button>

          {isochroneMutation.isSuccess && (
            <p className="text-sm text-emerald-600 dark:text-emerald-400">
              Isochrone rendered on map.
            </p>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}
