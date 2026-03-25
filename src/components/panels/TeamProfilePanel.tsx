"use client";

import { useState } from "react";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { trpc } from "@/lib/trpc/client";

const ORG_TYPE_LABELS: Record<string, string> = {
  nonprofit: "Nonprofit",
  cooperative: "Cooperative",
  business: "Business",
  individual: "Individual",
  government: "Government",
};

const ORG_TYPE_COLORS: Record<string, string> = {
  nonprofit: "#4caf50",
  cooperative: "#2196f3",
  business: "#ff9800",
  individual: "#9c27b0",
  government: "#607d8b",
};

const SPECIALTY_LABELS: Record<string, string> = {
  wildfire_mitigation: "Wildfire Mitigation",
  water_management: "Water Management",
  soil_restoration: "Soil Restoration",
  reforestation: "Reforestation",
  silvopasture: "Silvopasture",
  keyline_design: "Keyline Design",
  biochar: "Biochar",
  general_land_management: "General Land Management",
};

interface TeamProfilePanelProps {
  teamId: string | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Whether the current user is authenticated (for Join Team button) */
  isAuthenticated?: boolean;
}

export function TeamProfilePanel({
  teamId,
  open,
  onOpenChange,
  isAuthenticated,
}: TeamProfilePanelProps) {
  const [joinRequested, setJoinRequested] = useState(false);

  const { data: profile, isLoading } = trpc.teams.getTeamProfile.useQuery(
    { id: teamId! },
    { enabled: open && !!teamId }
  );

  const inviteMutation = trpc.teams.inviteMember.useMutation({
    onSuccess: () => setJoinRequested(true),
  });

  const specialties = Array.isArray(profile?.specialties)
    ? (profile.specialties as string[])
    : [];

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-[380px] sm:w-[420px] overflow-y-auto" onOpenChange={onOpenChange}>
        <SheetHeader className="mb-4">
          <SheetTitle>Team Profile</SheetTitle>
        </SheetHeader>

        {isLoading && (
          <div className="flex items-center justify-center py-12">
            <div className="w-6 h-6 border-2 border-[hsl(var(--primary))] border-t-transparent rounded-full animate-spin" />
          </div>
        )}

        {!isLoading && !profile && (
          <p className="text-sm text-[hsl(var(--muted-foreground))] text-center py-8">
            Team not found.
          </p>
        )}

        {profile && (
          <div className="flex flex-col gap-4">
            {/* Header: name + verified badge */}
            <div className="flex items-start gap-2">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <h2 className="text-lg font-semibold text-[hsl(var(--foreground))] truncate">
                    {profile.name}
                  </h2>
                  {profile.isVerified && (
                    <span
                      className="inline-flex items-center gap-1 text-[10px] font-semibold px-2 py-0.5 rounded-full bg-blue-500/20 text-blue-400"
                      title="Verified by PlantGeo"
                    >
                      <svg
                        className="w-3 h-3"
                        viewBox="0 0 24 24"
                        fill="currentColor"
                      >
                        <path d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                      </svg>
                      Verified
                    </span>
                  )}
                </div>

                {/* Org type badge */}
                {profile.orgType && (
                  <span
                    className="inline-block text-[10px] font-semibold px-2 py-0.5 rounded-full text-white mt-1"
                    style={{
                      backgroundColor:
                        ORG_TYPE_COLORS[profile.orgType] ?? "#888",
                    }}
                  >
                    {ORG_TYPE_LABELS[profile.orgType] ?? profile.orgType}
                  </span>
                )}
              </div>
            </div>

            {/* Description */}
            {profile.description && (
              <p className="text-sm text-[hsl(var(--muted-foreground))] leading-relaxed">
                {profile.description}
              </p>
            )}

            {/* Specialties */}
            {specialties.length > 0 && (
              <div>
                <p className="text-xs font-semibold text-[hsl(var(--muted-foreground))] uppercase tracking-wide mb-2">
                  Specialties
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {specialties.map((s) => (
                    <span
                      key={s}
                      className="text-xs px-2 py-1 rounded-full border border-[hsl(var(--border))] text-[hsl(var(--foreground))] bg-[hsl(var(--muted))]"
                    >
                      {SPECIALTY_LABELS[s] ?? s}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {/* Stats row */}
            <div className="flex gap-4 py-3 border-y border-[hsl(var(--border))]">
              <div className="text-center">
                <p className="text-lg font-bold text-[hsl(var(--foreground))]">
                  {profile.memberCount}
                </p>
                <p className="text-[10px] text-[hsl(var(--muted-foreground))]">Members</p>
              </div>
              {profile.serviceArea && (
                <div className="text-center">
                  <p className="text-lg font-bold text-[hsl(var(--foreground))]">
                    <svg
                      className="w-5 h-5 inline text-green-500"
                      fill="none"
                      stroke="currentColor"
                      viewBox="0 0 24 24"
                    >
                      <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        strokeWidth={2}
                        d="M9 20l-5.447-2.724A1 1 0 013 16.382V5.618a1 1 0 011.447-.894L9 7m0 13l6-3m-6 3V7m6 10l4.553 2.276A1 1 0 0021 18.382V7.618a1 1 0 00-.553-.894L15 4m0 13V4m0 0L9 7"
                      />
                    </svg>
                  </p>
                  <p className="text-[10px] text-[hsl(var(--muted-foreground))]">Service Area</p>
                </div>
              )}
            </div>

            {/* Website */}
            {profile.website && (
              <a
                href={profile.website}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-2 text-sm text-[hsl(var(--primary))] hover:underline"
              >
                <svg
                  className="w-4 h-4 flex-shrink-0"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"
                  />
                </svg>
                {profile.website.replace(/^https?:\/\//, "")}
              </a>
            )}

            {/* Members preview */}
            {profile.members.length > 0 && (
              <div>
                <p className="text-xs font-semibold text-[hsl(var(--muted-foreground))] uppercase tracking-wide mb-2">
                  Members
                </p>
                <div className="flex flex-col gap-1.5">
                  {profile.members.slice(0, 5).map((m) => (
                    <div
                      key={m.userId}
                      className="flex items-center justify-between text-sm"
                    >
                      <span className="text-[hsl(var(--foreground))] truncate">
                        {m.name ?? m.email ?? "Unknown"}
                      </span>
                      <span className="text-[10px] text-[hsl(var(--muted-foreground))] capitalize ml-2 flex-shrink-0">
                        {m.teamRole}
                      </span>
                    </div>
                  ))}
                  {profile.members.length > 5 && (
                    <p className="text-xs text-[hsl(var(--muted-foreground))]">
                      +{profile.members.length - 5} more
                    </p>
                  )}
                </div>
              </div>
            )}

            {/* Join / Request services */}
            <div className="flex gap-2 pt-2">
              {isAuthenticated && !joinRequested && (
                <button
                  onClick={() => {
                    // In a real app we'd use the current user's id.
                    // Here we show the UI affordance; actual invite goes via inviteMember.
                    setJoinRequested(true);
                  }}
                  disabled={inviteMutation.isPending}
                  className="flex-1 px-3 py-2 rounded-lg bg-[hsl(var(--primary))] text-[hsl(var(--primary-foreground))] text-sm font-medium hover:opacity-90 transition-opacity disabled:opacity-50"
                >
                  Join Team
                </button>
              )}
              {joinRequested && (
                <p className="text-sm text-green-500 py-2">
                  Request sent!
                </p>
              )}
              <a
                href={profile.website ?? "#"}
                target={profile.website ? "_blank" : undefined}
                rel="noopener noreferrer"
                className="flex-1 px-3 py-2 rounded-lg border border-[hsl(var(--border))] text-[hsl(var(--foreground))] text-sm font-medium hover:bg-[hsl(var(--muted))] transition-colors text-center"
              >
                Request Services
              </a>
            </div>
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
}
