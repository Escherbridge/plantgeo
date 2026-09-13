"use client";

import { useLandContextStore } from "@/stores/land-context-store";
import { DocumentedHelpSection } from "./DocumentedHelpSection";
import { DraftInquiry } from "./DraftInquiry";
import { EvidenceTimeSection } from "./EvidenceTimeSection";
import { PlaceDetailsSection } from "./PlaceDetailsSection";
import { RelatedAdvisersSection } from "./RelatedAdvisersSection";
import { RelevantPartiesSection } from "./RelevantPartiesSection";
import { RouteRationaleSection } from "./RouteRationaleSection";
import type { LandContextPanelData } from "./types";

interface LandContextPanelProps {
  data: LandContextPanelData | null;
  /** Simple close affordance; the map/store owner decides when this panel mounts at all. */
  onClose?: () => void;
}

/**
 * Persistent detail-panel content for a selected point/area. This component is deliberately a
 * thin content wrapper -- per this worker's brief, the sheet/dock chrome (open/close animation,
 * mobile-sheet behavior, focus restoration) belongs to whichever container mounts this, per the
 * existing "these are dock sections, not panels" convention in
 * `src/components/panels/AGENTS.md`. `onClose` is optional so a caller can either wire it to
 * real dismissal or omit it entirely for an always-mounted host.
 *
 * Draft inquiry only renders once relevant parties exist (`data.officeCards[0]`) so it always
 * has a real, source-backed recipient rather than a placeholder office.
 */
/**
 * Plain-language message for each `BudgetExceededResult["reason"]` value. Never surfaces the raw
 * enum string to the user -- see `src/lib/server/services/land-context/types.ts`.
 */
function budgetExceededMessage(budgetExceeded: NonNullable<
  ReturnType<typeof useLandContextStore.getState>["resultMeta"]
>["budgetExceeded"]): string {
  if (!budgetExceeded) return "";
  const { reason, limit, requested } = budgetExceeded;
  switch (reason) {
    case "aoi_area_exceeds_limit":
      return requested !== null
        ? `Your selected area is too large (${requested.toLocaleString()} sq degrees vs. a ${limit.toLocaleString()} sq degree budget) -- try a smaller area.`
        : `Your selected area is too large (over a ${limit.toLocaleString()} sq degree budget) -- try a smaller area.`;
    case "geometry_vertices_exceed_limit":
      return "Your selected area's shape is too detailed to process -- try a simpler or smaller area.";
    case "feature_count_would_exceed_limit":
      return "Your selected area contains too many results to show at once -- try a smaller area.";
    case "response_bytes_would_exceed_limit":
      return "Your selected area's results are too large to load at once -- try a smaller area.";
    case "outside_pilot_states":
      return "This location is outside the current pilot coverage area (Washington, Oregon, and Idaho only).";
    default:
      return "Your selection is too large to process -- try a smaller area.";
  }
}

export function LandContextPanel({ data, onClose }: LandContextPanelProps) {
  const budgetExceeded = useLandContextStore((state) => state.resultMeta?.budgetExceeded ?? null);
  const partialCoverage = useLandContextStore((state) => state.resultMeta?.partialCoverage ?? false);

  if (!data) {
    if (budgetExceeded) {
      return (
        <div className="p-4 text-xs text-amber-400" role="status">
          {budgetExceededMessage(budgetExceeded)}
        </div>
      );
    }
    return (
      <div className="p-4 text-xs text-zinc-500">
        Select a point or bounded area on the map to see place details and public routes.
      </div>
    );
  }

  const primaryRelationship = data.officeCards[0]?.relationships[0];
  const primaryOffice = data.officeCards[0]?.office;

  return (
    <div className="flex flex-col gap-4 p-4">
      {partialCoverage ? (
        <div className="text-xs text-zinc-400" role="status">
          This area only has partial source coverage -- some results may be missing.
        </div>
      ) : null}
      {onClose ? (
        <div className="flex justify-end">
          <button
            type="button"
            onClick={onClose}
            aria-label="Close land context panel"
            className="text-xs text-zinc-500 hover:text-zinc-300"
          >
            Close
          </button>
        </div>
      ) : null}

      <PlaceDetailsSection place={data.place} />
      <RelevantPartiesSection officeCards={data.officeCards} />
      <RouteRationaleSection officeCards={data.officeCards} />
      <DocumentedHelpSection officeCards={data.officeCards} />
      <EvidenceTimeSection evidence={data.evidence} />
      <RelatedAdvisersSection advisers={data.advisers} />

      {primaryOffice && primaryRelationship ? (
        <section aria-labelledby="land-context-draft-heading">
          <h3 id="land-context-draft-heading" className="text-sm font-semibold text-zinc-100">
            Draft an inquiry
          </h3>
          <div className="mt-2">
            <DraftInquiry
              input={{
                place: data.place,
                recipient: primaryOffice,
                recipientRationale: primaryRelationship.evidence.relationshipEvidence,
                routeMeaning: primaryRelationship.routeMeaning,
                suggestedQuestion:
                  primaryRelationship.routeMeaning === "records_assistance"
                    ? "Which records or office handles this inquiry, and is there an established contact process?"
                    : "What is the appropriate next step to discuss this location with your office?",
                sourceLinks: [
                  ...(data.place.officialRecordUrl
                    ? [{ label: data.place.officialRecordLabel ?? "Official source record", url: data.place.officialRecordUrl }]
                    : []),
                  ...(primaryRelationship.evidence.sourceUrl
                    ? [{ label: primaryRelationship.evidence.sourceLabel, url: primaryRelationship.evidence.sourceUrl }]
                    : []),
                ],
              }}
            />
          </div>
        </section>
      ) : null}
    </div>
  );
}
