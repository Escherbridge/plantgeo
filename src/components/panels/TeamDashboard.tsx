"use client";

import { useRef, useState } from "react";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { trpc } from "@/lib/trpc/client";
import { ServiceAreaDrawTool } from "@/components/tools/ServiceAreaDrawTool";
import type { Map as MapLibreMap } from "maplibre-gl";

// ── Constants ─────────────────────────────────────────────────────────────────

const ORG_TYPES = [
  { value: "nonprofit", label: "Nonprofit" },
  { value: "cooperative", label: "Cooperative" },
  { value: "business", label: "Business" },
  { value: "individual", label: "Individual" },
  { value: "government", label: "Government" },
] as const;

const SPECIALTIES = [
  { value: "wildfire_mitigation", label: "Wildfire Mitigation" },
  { value: "water_management", label: "Water Management" },
  { value: "soil_restoration", label: "Soil Restoration" },
  { value: "reforestation", label: "Reforestation" },
  { value: "silvopasture", label: "Silvopasture" },
  { value: "keyline_design", label: "Keyline Design" },
  { value: "biochar", label: "Biochar" },
  { value: "general_land_management", label: "General Land Management" },
] as const;

const STRATEGY_LABELS: Record<string, string> = {
  keyline: "Keyline Design",
  silvopasture: "Silvopasture",
  reforestation: "Reforestation",
  biochar: "Biochar",
  water_harvesting: "Water Harvesting",
  cover_cropping: "Cover Cropping",
};

type Tab = "overview" | "members" | "service-area" | "settings";

// ── Settings form ─────────────────────────────────────────────────────────────

interface SettingsFormProps {
  teamId: string;
  initialValues: {
    name: string;
    description?: string | null;
    orgType?: string | null;
    specialties?: string[] | null;
    website?: string | null;
  };
  onSaved: () => void;
}

function SettingsForm({ teamId, initialValues, onSaved }: SettingsFormProps) {
  const [name, setName] = useState(initialValues.name);
  const [description, setDescription] = useState(initialValues.description ?? "");
  const [orgType, setOrgType] = useState(initialValues.orgType ?? "");
  const [selectedSpecialties, setSelectedSpecialties] = useState<string[]>(
    initialValues.specialties ?? []
  );
  const [website, setWebsite] = useState(initialValues.website ?? "");
  const [error, setError] = useState<string | null>(null);

  const updateMutation = trpc.teams.updateTeam.useMutation({
    onSuccess: () => {
      setError(null);
      onSaved();
    },
    onError: (e) => setError(e.message),
  });

  const toggleSpecialty = (value: string) => {
    setSelectedSpecialties((prev) =>
      prev.includes(value) ? prev.filter((s) => s !== value) : [...prev, value]
    );
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    updateMutation.mutate({
      id: teamId,
      name: name.trim(),
      description: description.trim() || undefined,
      orgType: (orgType as "nonprofit" | "cooperative" | "business" | "individual" | "government") || undefined,
      specialties: selectedSpecialties,
      website: website.trim() || undefined,
    });
  };

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-4">
      {error && (
        <p className="text-xs text-red-500 bg-red-500/10 rounded-lg px-3 py-2">{error}</p>
      )}

      <div className="flex flex-col gap-1">
        <label className="text-xs font-semibold text-[hsl(var(--muted-foreground))] uppercase tracking-wide">
          Team Name *
        </label>
        <input
          required
          value={name}
          onChange={(e) => setName(e.target.value)}
          className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] text-[hsl(var(--foreground))] px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-[hsl(var(--ring))]"
        />
      </div>

      <div className="flex flex-col gap-1">
        <label className="text-xs font-semibold text-[hsl(var(--muted-foreground))] uppercase tracking-wide">
          Description
        </label>
        <textarea
          rows={3}
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] text-[hsl(var(--foreground))] px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-[hsl(var(--ring))] resize-none"
        />
      </div>

      <div className="flex flex-col gap-1">
        <label className="text-xs font-semibold text-[hsl(var(--muted-foreground))] uppercase tracking-wide">
          Organization Type
        </label>
        <select
          value={orgType}
          onChange={(e) => setOrgType(e.target.value)}
          className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] text-[hsl(var(--foreground))] px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-[hsl(var(--ring))]"
        >
          <option value="">Select type…</option>
          {ORG_TYPES.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </div>

      <div className="flex flex-col gap-2">
        <label className="text-xs font-semibold text-[hsl(var(--muted-foreground))] uppercase tracking-wide">
          Specialties
        </label>
        <div className="flex flex-wrap gap-1.5">
          {SPECIALTIES.map((s) => (
            <button
              key={s.value}
              type="button"
              onClick={() => toggleSpecialty(s.value)}
              className={`text-xs px-2 py-1 rounded-full border transition-colors ${
                selectedSpecialties.includes(s.value)
                  ? "border-[hsl(var(--primary))] bg-[hsl(var(--primary))] text-[hsl(var(--primary-foreground))]"
                  : "border-[hsl(var(--border))] text-[hsl(var(--foreground))] hover:bg-[hsl(var(--muted))]"
              }`}
            >
              {s.label}
            </button>
          ))}
        </div>
      </div>

      <div className="flex flex-col gap-1">
        <label className="text-xs font-semibold text-[hsl(var(--muted-foreground))] uppercase tracking-wide">
          Website
        </label>
        <input
          type="url"
          value={website}
          onChange={(e) => setWebsite(e.target.value)}
          placeholder="https://example.org"
          className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] text-[hsl(var(--foreground))] px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-[hsl(var(--ring))]"
        />
      </div>

      <button
        type="submit"
        disabled={updateMutation.isPending}
        className="px-4 py-2 rounded-lg bg-[hsl(var(--primary))] text-[hsl(var(--primary-foreground))] text-sm font-medium hover:opacity-90 transition-opacity disabled:opacity-50"
      >
        {updateMutation.isPending ? "Saving…" : "Save Settings"}
      </button>
    </form>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

interface TeamDashboardProps {
  teamId: string | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  mapRef?: React.RefObject<MapLibreMap | null>;
}

export function TeamDashboard({
  teamId,
  open,
  onOpenChange,
  mapRef,
}: TeamDashboardProps) {
  const [activeTab, setActiveTab] = useState<Tab>("overview");

  const { data: dashboard, isLoading, refetch } = trpc.teams.getTeamDashboard.useQuery(
    { teamId: teamId! },
    { enabled: open && !!teamId }
  );

  const inviteMutation = trpc.teams.inviteMember.useMutation({
    onSuccess: () => refetch(),
  });

  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteError, setInviteError] = useState<string | null>(null);

  const handleInvite = (e: React.FormEvent) => {
    e.preventDefault();
    setInviteError(null);
    // In a full implementation, look up userId by email via a separate procedure.
    // For now, the UI shows the field; actual lookup requires a users.findByEmail endpoint.
    setInviteError("Email invite lookup not yet implemented — use user ID directly.");
  };

  const tabs: { id: Tab; label: string }[] = [
    { id: "overview", label: "Overview" },
    { id: "members", label: "Members" },
    { id: "service-area", label: "Service Area" },
    { id: "settings", label: "Settings" },
  ];

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-[420px] sm:w-[480px] overflow-y-auto" onOpenChange={onOpenChange}>
        <SheetHeader className="mb-4">
          <SheetTitle>
            {dashboard?.team.name ?? "Team Dashboard"}
          </SheetTitle>
        </SheetHeader>

        {isLoading && (
          <div className="flex items-center justify-center py-12">
            <div className="w-6 h-6 border-2 border-[hsl(var(--primary))] border-t-transparent rounded-full animate-spin" />
          </div>
        )}

        {!isLoading && !dashboard && (
          <p className="text-sm text-[hsl(var(--muted-foreground))] text-center py-8">
            Unable to load team dashboard.
          </p>
        )}

        {dashboard && (
          <>
            {/* Tab bar */}
            <div className="flex gap-1 mb-5 border-b border-[hsl(var(--border))] pb-0">
              {tabs.map((tab) => (
                <button
                  key={tab.id}
                  onClick={() => setActiveTab(tab.id)}
                  className={`px-3 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
                    activeTab === tab.id
                      ? "border-[hsl(var(--primary))] text-[hsl(var(--primary))]"
                      : "border-transparent text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--foreground))]"
                  }`}
                >
                  {tab.label}
                </button>
              ))}
            </div>

            {/* ── Overview tab ── */}
            {activeTab === "overview" && (
              <div className="flex flex-col gap-4">
                {/* Stats */}
                <div className="grid grid-cols-2 gap-3">
                  <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-3 text-center">
                    <p className="text-2xl font-bold text-[hsl(var(--foreground))]">
                      {dashboard.members.length}
                    </p>
                    <p className="text-xs text-[hsl(var(--muted-foreground))]">Members</p>
                  </div>
                  <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-3 text-center">
                    <p className="text-2xl font-bold text-[hsl(var(--foreground))]">
                      {dashboard.priorityZones.length}
                    </p>
                    <p className="text-xs text-[hsl(var(--muted-foreground))]">Priority Zones</p>
                  </div>
                </div>

                {/* Team info */}
                <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-3 flex flex-col gap-2">
                  {dashboard.team.description && (
                    <p className="text-sm text-[hsl(var(--muted-foreground))]">
                      {dashboard.team.description}
                    </p>
                  )}
                  {dashboard.team.orgType && (
                    <p className="text-xs text-[hsl(var(--muted-foreground))]">
                      Type: <span className="text-[hsl(var(--foreground))] capitalize">{dashboard.team.orgType}</span>
                    </p>
                  )}
                  {dashboard.team.isVerified && (
                    <div className="flex items-center gap-1 text-xs text-blue-400">
                      <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="currentColor">
                        <path d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                      </svg>
                      Verified team
                    </div>
                  )}
                </div>

                {/* Priority zones in service area */}
                {dashboard.priorityZones.length > 0 && (
                  <div>
                    <p className="text-xs font-semibold text-[hsl(var(--muted-foreground))] uppercase tracking-wide mb-2">
                      Priority Zones in Service Area
                    </p>
                    <div className="flex flex-col gap-2">
                      {dashboard.priorityZones.slice(0, 5).map((zone) => (
                        <div
                          key={zone.id}
                          className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-2.5 flex items-center justify-between"
                        >
                          <div>
                            <p className="text-sm font-medium text-[hsl(var(--foreground))]">
                              {STRATEGY_LABELS[zone.strategyType] ?? zone.strategyType}
                            </p>
                            <p className="text-xs text-[hsl(var(--muted-foreground))]">
                              {zone.requestCount} requests · {zone.totalVotes} votes
                            </p>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {dashboard.priorityZones.length === 0 && dashboard.team.serviceArea && (
                  <p className="text-sm text-[hsl(var(--muted-foreground))] text-center py-4">
                    No priority zones found in your service area yet.
                  </p>
                )}

                {!dashboard.team.serviceArea && (
                  <div className="rounded-lg border border-dashed border-[hsl(var(--border))] p-4 text-center">
                    <p className="text-sm text-[hsl(var(--muted-foreground))]">
                      Define a service area to see community priority zones.
                    </p>
                    <button
                      onClick={() => setActiveTab("service-area")}
                      className="mt-2 text-sm text-[hsl(var(--primary))] hover:underline"
                    >
                      Set up service area →
                    </button>
                  </div>
                )}
              </div>
            )}

            {/* ── Members tab ── */}
            {activeTab === "members" && (
              <div className="flex flex-col gap-4">
                <div className="flex flex-col gap-2">
                  {dashboard.members.map((m) => (
                    <div
                      key={m.userId}
                      className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-3 flex items-center justify-between"
                    >
                      <div>
                        <p className="text-sm font-medium text-[hsl(var(--foreground))]">
                          {m.name ?? "Unknown"}
                        </p>
                        <p className="text-xs text-[hsl(var(--muted-foreground))]">
                          {m.email}
                        </p>
                      </div>
                      <span className="text-xs capitalize text-[hsl(var(--muted-foreground))] border border-[hsl(var(--border))] px-2 py-0.5 rounded-full">
                        {m.teamRole}
                      </span>
                    </div>
                  ))}
                </div>

                {/* Invite by email */}
                {dashboard.memberRole === "owner" && (
                  <form onSubmit={handleInvite} className="flex flex-col gap-2 pt-2 border-t border-[hsl(var(--border))]">
                    <p className="text-xs font-semibold text-[hsl(var(--muted-foreground))] uppercase tracking-wide">
                      Invite Member
                    </p>
                    {inviteError && (
                      <p className="text-xs text-red-500 bg-red-500/10 rounded px-2 py-1">
                        {inviteError}
                      </p>
                    )}
                    <div className="flex gap-2">
                      <input
                        type="email"
                        value={inviteEmail}
                        onChange={(e) => setInviteEmail(e.target.value)}
                        placeholder="member@example.com"
                        className="flex-1 rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] text-[hsl(var(--foreground))] px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-[hsl(var(--ring))]"
                      />
                      <button
                        type="submit"
                        disabled={!inviteEmail.trim() || inviteMutation.isPending}
                        className="px-3 py-2 rounded-lg bg-[hsl(var(--primary))] text-[hsl(var(--primary-foreground))] text-sm font-medium hover:opacity-90 transition-opacity disabled:opacity-50"
                      >
                        Invite
                      </button>
                    </div>
                  </form>
                )}
              </div>
            )}

            {/* ── Service Area tab ── */}
            {activeTab === "service-area" && (
              <div className="flex flex-col gap-3">
                <p className="text-sm text-[hsl(var(--muted-foreground))]">
                  Draw your team&apos;s service area polygon directly on the map.
                  Click to add vertices, double-click to close.
                </p>
                <ServiceAreaDrawTool
                  teamId={dashboard.team.id}
                  existingServiceArea={
                    dashboard.team.serviceArea as Record<string, unknown> | null
                  }
                  mapRef={mapRef}
                  onSaved={() => refetch()}
                />
              </div>
            )}

            {/* ── Settings tab ── */}
            {activeTab === "settings" && (
              <SettingsForm
                teamId={dashboard.team.id}
                initialValues={{
                  name: dashboard.team.name,
                  description: dashboard.team.description,
                  orgType: dashboard.team.orgType,
                  specialties: Array.isArray(dashboard.team.specialties)
                    ? (dashboard.team.specialties as string[])
                    : [],
                  website: dashboard.team.website,
                }}
                onSaved={() => refetch()}
              />
            )}
          </>
        )}
      </SheetContent>
    </Sheet>
  );
}
