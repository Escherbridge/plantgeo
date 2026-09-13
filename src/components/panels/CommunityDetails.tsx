"use client";

import { useState } from "react";
import { trpc } from "@/lib/trpc/client";
import { RequestSubmitModal } from "@/components/panels/RequestSubmitModal";
import { InterventionSubmitModal } from "@/components/panels/InterventionSubmitModal";
import { useAuthStore } from "@/stores/auth-store";
import { invalidateInterventionDraftsOverlay } from "@/lib/map/use-intervention-drafts";

// The panel's own STRATEGY_TYPES/STRATEGY_COLORS/STRATEGY_LABELS tables went with the request
// list they filtered and painted (`public_strategy_requests_20260913` Phase 3). They were a
// hand-synced second copy of `InterventionType` -- exactly the duplication OQ-D unified away --
// and a request's colour is now decided once, on the map, by `INTERVENTION_REQUEST_COLOR`.

const INTERVENTION_STATUS_LABELS: Record<string, string> = {
  pending_review: "In review",
  published: "Approved",
  rejected: "Not accepted",
};

const INTERVENTION_TYPE_LABELS: Record<string, string> = {
  reforestation: "Reforestation",
  silvopasture: "Silvopasture",
  cover_cropping: "Cover Cropping",
  biochar: "Biochar",
  keyline: "Keyline Design",
};

/** Narrows the jsonb properties bag a submission row carries. */
function readInterventionSummary(properties: unknown): {
  name: string;
  type: string;
} {
  const bag = (properties ?? {}) as { name?: unknown; type?: unknown };
  return {
    name: typeof bag.name === "string" ? bag.name : "Untitled site",
    type: typeof bag.type === "string" ? bag.type : "",
  };
}

interface CommunityDetailsProps {
  /** Map centre both submission flows pin to. */
  mapCenter?: { lat: number; lon: number };
}

/**
 * The reader's own intervention submissions plus the two submission flows, as the Community
 * section of the map dock. Mounted only while that section is expanded, which is what every
 * `enabled` flag below now reads in place of the sheet's `open` prop; the two `<LayerToggle>`
 * rows went with the sheet, since the section's own layer rows are the switches.
 *
 * Strategy requests no longer list here. Phase 3 of `public_strategy_requests_20260913` made a
 * request a published map feature rather than a private row, so the panel keeps the "+ Request"
 * entry point and points at the map for the reading; the distance/filter machinery that served
 * the private list went with it.
 */
export function CommunityDetails({ mapCenter }: CommunityDetailsProps) {
  const [showSubmitModal, setShowSubmitModal] = useState(false);
  const [showInterventionModal, setShowInterventionModal] = useState(false);
  const { activeTeamId } = useAuthStore();
  const { data: memberships } = trpc.teams.listMyTeams.useQuery(undefined);
  const activeMembership = memberships?.find(
    ({ team }) => team.id === activeTeamId
  );
  const activeTeam = activeMembership?.team;
  const canSubmitToActiveTeam =
    !activeTeamId ||
    activeMembership?.role === "owner" ||
    activeMembership?.role === "member";

  const {
    data: interventionSubmissions,
    refetch: refetchInterventions,
  } = trpc.interventions.listMySubmissions.useQuery({
    teamId: activeTeamId ?? undefined,
    limit: 25,
  });

  // Refetches this panel's own query above; the map's signed-in draft/proposed overlay
  // (useInterventionDraftsOverlay) reads listMySubmissions/listProposed under different query
  // input and needs its own invalidation so a just-submitted polygon appears without reload.
  const trpcUtils = trpc.useUtils();

  const submitLat = mapCenter?.lat ?? 0;
  const submitLon = mapCenter?.lon ?? 0;

  return (
    <>
      <div className="flex flex-col">
        {/* Names whose submissions the recommendation list below holds. It used to name whose
            REQUESTS these were, which stopped being a meaningful distinction the moment a request
            became a public map feature every reader sees the same way. */}
        <h3 className="mb-3 text-sm font-semibold text-[hsl(var(--foreground))]">
          {activeTeamId
            ? `${activeTeam?.name ?? "Partner workspace"} submissions`
            : "Your submissions"}
        </h3>

        {/* Intervention recommendations */}
        <section className="mt-4 mb-5 rounded-lg border border-[hsl(var(--border))] p-3">
          <div className="flex items-center justify-between gap-2 mb-2">
            <h3 className="text-sm font-medium text-[hsl(var(--foreground))]">
              Intervention recommendations
            </h3>
            <button
              type="button"
              onClick={() => setShowInterventionModal(true)}
              disabled={!canSubmitToActiveTeam}
              title={
                canSubmitToActiveTeam
                  ? undefined
                  : "Only a workspace owner or member can recommend a shared intervention"
              }
              className="px-3 py-2 min-h-11 rounded-lg border border-[hsl(var(--border))] text-sm text-[hsl(var(--foreground))] hover:bg-[hsl(var(--muted))] transition-colors whitespace-nowrap disabled:opacity-50"
            >
              + Recommend
            </button>
          </div>

          {/* The two submit paths in this panel write to different tables with different
              fates, and only this one can ever reach the map. Each says which it is. */}
          <p className="text-xs text-[hsl(var(--muted-foreground))] mb-2">
            Proposes a site for the public map. Recommendations are held for
            expert review and only appear on the map once a reviewer approves
            them.
          </p>

          {!interventionSubmissions || interventionSubmissions.length === 0 ? (
            <p className="text-xs text-[hsl(var(--muted-foreground))] py-2">
              {activeTeamId
                ? "This partner workspace has not recommended any interventions yet."
                : "You have not recommended any interventions yet."}
            </p>
          ) : (
            <ul className="flex flex-col gap-2">
              {interventionSubmissions.map((submission) => {
                const { name, type } = readInterventionSummary(
                  submission.properties
                );
                const status = submission.status ?? "pending_review";
                return (
                  <li
                    key={submission.id}
                    className="rounded-lg bg-[hsl(var(--card))] px-3 py-2"
                  >
                    <p className="text-sm text-[hsl(var(--foreground))] truncate">
                      {name}
                    </p>
                    <p className="text-[10px] text-[hsl(var(--muted-foreground))]">
                      {INTERVENTION_TYPE_LABELS[type] ?? type}
                      {type && " · "}
                      {INTERVENTION_STATUS_LABELS[status] ?? status}
                    </p>
                    {status === "rejected" && submission.reviewNote && (
                      <p className="text-[10px] text-[hsl(var(--muted-foreground))] mt-1">
                        Reviewer note: {submission.reviewNote}
                      </p>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </section>

        {/* Strategy requests. There is no list here any more, and its absence is the
            change, not an omission: a request is now a published `geo.features` row on
            the same merged layer this panel's recommendations land on (painted its own
            blue by `INTERVENTION_REQUEST_COLOR`), so the map IS the list. A second,
            panel-local copy of it would be a list of public objects that only the
            submitter could see -- exactly the shape Phase 3 retired. */}
        <section className="mt-1 rounded-lg border border-[hsl(var(--border))] p-3">
          <div className="flex items-center justify-between gap-2 mb-2">
            <h3 className="text-sm font-medium text-[hsl(var(--foreground))]">
              Strategy requests
            </h3>
            <button
              type="button"
              onClick={() => setShowSubmitModal(true)}
              className="px-3 py-2 min-h-11 rounded-lg bg-[hsl(var(--primary))] text-[hsl(var(--primary-foreground))] text-sm font-medium hover:opacity-90 transition-opacity whitespace-nowrap"
            >
              + Request
            </button>
          </div>

          <p className="text-xs text-[hsl(var(--muted-foreground))]">
            Asks for a strategy at the map&rsquo;s centre point &mdash; &ldquo;this
            area could use X.&rdquo; A request is published straight away with no
            review queue, and appears on the map in its own colour. Click one on
            the map to read it, comment on it, or back it.
          </p>
        </section>
      </div>

      {showSubmitModal && (
        <RequestSubmitModal
          lat={submitLat}
          lon={submitLon}
          onClose={() => setShowSubmitModal(false)}
          // A request is published on write, so the surface that must refresh is the MAP, not a
          // panel list -- the same overlay invalidation a recommendation triggers below.
          onSuccess={() => {
            void invalidateInterventionDraftsOverlay(trpcUtils);
          }}
        />
      )}

      {showInterventionModal && (
        <InterventionSubmitModal
          lat={submitLat}
          lon={submitLon}
          teamId={activeTeamId ?? undefined}
          workspaceName={activeTeam?.name}
          onClose={() => setShowInterventionModal(false)}
          onSuccess={() => {
            refetchInterventions();
            void invalidateInterventionDraftsOverlay(trpcUtils);
          }}
        />
      )}
    </>
  );
}
