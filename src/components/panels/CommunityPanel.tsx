"use client";

import { useState } from "react";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { trpc } from "@/lib/trpc/client";
import { RequestSubmitModal } from "@/components/panels/RequestSubmitModal";
import { LayerToggle } from "@/components/ui/layer-toggle";

const STRATEGY_TYPES = [
  { value: "", label: "All Types" },
  { value: "keyline", label: "Keyline Design" },
  { value: "silvopasture", label: "Silvopasture" },
  { value: "reforestation", label: "Reforestation" },
  { value: "biochar", label: "Biochar" },
  { value: "water_harvesting", label: "Water Harvesting" },
  { value: "cover_cropping", label: "Cover Cropping" },
] as const;

const STRATEGY_COLORS: Record<string, string> = {
  keyline: "#2196f3",
  silvopasture: "#4caf50",
  reforestation: "#8bc34a",
  biochar: "#795548",
  water_harvesting: "#00bcd4",
  cover_cropping: "#ff9800",
};

const STRATEGY_LABELS: Record<string, string> = {
  keyline: "Keyline",
  silvopasture: "Silvopasture",
  reforestation: "Reforestation",
  biochar: "Biochar",
  water_harvesting: "Water Harvesting",
  cover_cropping: "Cover Cropping",
};

interface CommunityPanelProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Current map center for distance calculation */
  mapCenter?: { lat: number; lon: number };
  bbox?: string;
}

function haversineKm(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const R = 6371;
  const dLat = ((lat2 - lat1) * Math.PI) / 180;
  const dLon = ((lon2 - lon1) * Math.PI) / 180;
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos((lat1 * Math.PI) / 180) *
      Math.cos((lat2 * Math.PI) / 180) *
      Math.sin(dLon / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

export function CommunityPanel({
  open,
  onOpenChange,
  mapCenter,
  bbox,
}: CommunityPanelProps) {
  const [strategyFilter, setStrategyFilter] = useState("");
  const [showSubmitModal, setShowSubmitModal] = useState(false);

  const { data: requests, refetch } = trpc.community.getRequests.useQuery(
    {
      bbox,
      strategyType: strategyFilter || undefined,
      limit: 50,
    },
    { enabled: open }
  );

  const voteMutation = trpc.community.voteOnRequest.useMutation({
    onSuccess: () => refetch(),
  });

  const submitLat = mapCenter?.lat ?? 0;
  const submitLon = mapCenter?.lon ?? 0;

  return (
    <>
      <Sheet open={open} onOpenChange={onOpenChange}>
        <SheetContent side="right" className="w-[380px] sm:w-[420px] overflow-y-auto" onOpenChange={onOpenChange}>
          <SheetHeader className="mb-4">
            <SheetTitle>Community Strategy Requests</SheetTitle>
          </SheetHeader>

          <LayerToggle layerId="demand-heatmap" label="Demand Heatmap" />

          {/* Filter + Submit */}
          <div className="flex gap-2 mb-4">
            <select
              value={strategyFilter}
              onChange={(e) => setStrategyFilter(e.target.value)}
              className="flex-1 rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] text-[hsl(var(--foreground))] px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-[hsl(var(--ring))]"
            >
              {STRATEGY_TYPES.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </select>
            <button
              onClick={() => setShowSubmitModal(true)}
              className="px-3 py-2 rounded-lg bg-[hsl(var(--primary))] text-[hsl(var(--primary-foreground))] text-sm font-medium hover:opacity-90 transition-opacity whitespace-nowrap"
            >
              + Submit
            </button>
          </div>

          {/* Request list */}
          <div className="flex flex-col gap-2">
            {!requests || requests.length === 0 ? (
              <p className="text-sm text-[hsl(var(--muted-foreground))] text-center py-8">
                No strategy requests found in this area.
              </p>
            ) : (
              requests.map((req) => {
                const distKm =
                  mapCenter
                    ? haversineKm(mapCenter.lat, mapCenter.lon, req.lat, req.lon)
                    : null;

                return (
                  <div
                    key={req.id}
                    className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-3 flex flex-col gap-2"
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium text-[hsl(var(--foreground))] truncate">
                          {req.title}
                        </p>
                        <div className="flex items-center gap-2 mt-1">
                          <span
                            className="inline-block text-[10px] font-semibold px-2 py-0.5 rounded-full text-white"
                            style={{
                              backgroundColor:
                                STRATEGY_COLORS[req.strategyType] ?? "#888",
                            }}
                          >
                            {STRATEGY_LABELS[req.strategyType] ?? req.strategyType}
                          </span>
                          {distKm !== null && (
                            <span className="text-[10px] text-[hsl(var(--muted-foreground))]">
                              {distKm < 1
                                ? `${Math.round(distKm * 1000)}m away`
                                : `${distKm.toFixed(1)}km away`}
                            </span>
                          )}
                        </div>
                        {req.description && (
                          <p className="text-xs text-[hsl(var(--muted-foreground))] mt-1 line-clamp-2">
                            {req.description}
                          </p>
                        )}
                      </div>

                      {/* Vote button */}
                      <button
                        onClick={() => voteMutation.mutate({ requestId: req.id })}
                        disabled={voteMutation.isPending}
                        className="flex flex-col items-center gap-0.5 px-2 py-1 rounded-lg border border-[hsl(var(--border))] hover:bg-[hsl(var(--muted))] transition-colors min-w-[44px] disabled:opacity-50"
                        title="Vote for this request"
                      >
                        <svg className="w-3.5 h-3.5 text-[hsl(var(--muted-foreground))]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 15l7-7 7 7" />
                        </svg>
                        <span className="text-xs font-bold text-[hsl(var(--foreground))]">
                          {req.voteCount ?? 0}
                        </span>
                      </button>
                    </div>

                    <p className="text-[10px] text-[hsl(var(--muted-foreground))]">
                      {req.lat.toFixed(4)}, {req.lon.toFixed(4)}
                      {req.createdAt && (
                        <> &middot; {new Date(req.createdAt).toLocaleDateString()}</>
                      )}
                    </p>
                  </div>
                );
              })
            )}
          </div>
        </SheetContent>
      </Sheet>

      {showSubmitModal && (
        <RequestSubmitModal
          lat={submitLat}
          lon={submitLon}
          onClose={() => setShowSubmitModal(false)}
          onSuccess={() => refetch()}
        />
      )}
    </>
  );
}
