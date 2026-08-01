"use client";

import { useState } from "react";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { trpc } from "@/lib/trpc/client";
import { RequestSubmitModal } from "@/components/panels/RequestSubmitModal";
import { LayerToggle } from "@/components/ui/layer-toggle";
import { useAuthStore } from "@/stores/auth-store";

const STRATEGY_TYPES = [
  { value: "", label: "All Types" },
  { value: "keyline", label: "Keyline Design" },
  { value: "silvopasture", label: "Silvopasture" },
  { value: "reforestation", label: "Reforestation" },
  { value: "biochar", label: "Biochar" },
  { value: "water_harvesting", label: "Water Harvesting" },
  { value: "cover_cropping", label: "Cover Cropping" },
] as const;

type StrategyFilter = (typeof STRATEGY_TYPES)[number]["value"];

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
  const [strategyFilter, setStrategyFilter] = useState<StrategyFilter>("");
  const [showSubmitModal, setShowSubmitModal] = useState(false);
  const { activeTeamId } = useAuthStore();
  const { data: memberships } = trpc.teams.listMyTeams.useQuery(undefined, {
    enabled: open,
  });
  const activeMembership = memberships?.find(
    ({ team }) => team.id === activeTeamId
  );
  const activeTeam = activeMembership?.team;
  const canSubmitToActiveTeam =
    !activeTeamId ||
    activeMembership?.role === "owner" ||
    activeMembership?.role === "member";

  const {
    data: requests,
    error: requestsError,
    refetch,
  } = trpc.community.getRequests.useQuery(
    {
      bbox,
      strategyType: strategyFilter === "" ? undefined : strategyFilter,
      teamId: activeTeamId ?? undefined,
      limit: 50,
    },
    { enabled: open }
  );

  const submitLat = mapCenter?.lat ?? 0;
  const submitLon = mapCenter?.lon ?? 0;

  return (
    <>
      <Sheet open={open} onOpenChange={onOpenChange}>
        <SheetContent side="right" className="w-[380px] sm:w-[420px] overflow-y-auto" onOpenChange={onOpenChange}>
          <SheetHeader className="mb-4">
            <SheetTitle>
              {activeTeamId
                ? `${activeTeam?.name ?? "Partner workspace"} strategy requests`
                : "Your strategy requests"}
            </SheetTitle>
          </SheetHeader>

          <LayerToggle layerId="demand-heatmap" label="Demand Heatmap" />
          <LayerToggle layerId="interventions" label="Interventions" />

          {/* Filter + Submit */}
          <div className="flex gap-2 mb-4">
            <select
              value={strategyFilter}
              onChange={(e) =>
                setStrategyFilter(e.target.value as StrategyFilter)
              }
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
              disabled={!canSubmitToActiveTeam}
              title={
                canSubmitToActiveTeam
                  ? undefined
                  : "Only a workspace owner or member can submit a shared request"
              }
              className="px-3 py-2 rounded-lg bg-[hsl(var(--primary))] text-[hsl(var(--primary-foreground))] text-sm font-medium hover:opacity-90 transition-opacity whitespace-nowrap disabled:opacity-50"
            >
              + Submit
            </button>
          </div>

          <p className="mb-3 text-xs text-[hsl(var(--muted-foreground))]">
            {activeTeamId
              ? `Requests are shared only with authenticated members of ${
                  activeTeam?.name ?? "the selected partner workspace"
                }.`
              : "Requests are private to your account."}{" "}
            Submitting one does not publish its location or create a public
            waypoint.
          </p>

          {activeTeamId && !canSubmitToActiveTeam && (
            <p role="status" className="mb-3 text-xs text-[hsl(var(--muted-foreground))]">
              You can view this workspace, but only an owner or member can
              submit a shared request.
            </p>
          )}

            {/* Request list */}
            <div className="flex flex-col gap-2">
            {requestsError ? (
              <p role="alert" className="text-sm text-[hsl(var(--muted-foreground))] text-center py-8">
                Sign in and select a workspace you belong to before viewing
                strategy requests.
              </p>
            ) : !requests || requests.length === 0 ? (
              <p className="text-sm text-[hsl(var(--muted-foreground))] text-center py-8">
                {activeTeamId
                  ? "No strategy requests from this partner workspace are in this area."
                  : "No private strategy requests are in this area."}
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

                    </div>

                    <p className="text-[10px] text-[hsl(var(--muted-foreground))]">
                      Private location
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
          teamId={activeTeamId ?? undefined}
          workspaceName={activeTeam?.name}
          onClose={() => setShowSubmitModal(false)}
          onSuccess={() => refetch()}
        />
      )}
    </>
  );
}
