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
  /**
   * Multi-candidate browsing (store's `results`/`candidateIndex`). All four are optional so a
   * caller without a candidate list (e.g. a future non-store-backed host) can omit browsing
   * entirely -- the controls only render when `resultsCount` is provided and > 1.
   */
  resultsCount?: number;
  candidateIndex?: number | null;
  onFocusPreviousCandidate?: () => void;
  onFocusNextCandidate?: () => void;
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

export function LandContextPanel({
  data,
  onClose,
  resultsCount,
  candidateIndex = null,
  onFocusPreviousCandidate,
  onFocusNextCandidate,
}: LandContextPanelProps) {
  const budgetExceeded = useLandContextStore((state) => state.resultMeta?.budgetExceeded ?? null);
  // `resultMeta.partialCoverage` never co-occurs with a matched feature: `readBoundedAoiIntersection`
  // (`src/lib/server/services/land-context/reader.ts`) only ever reports `partial_area_coverage`
  // in the branch where `features.length === 0`, so the old `data`-gated caveat below never rendered
  // in production (N11). `coverageNotices` carries the same state and is populated on exactly the
  // no-match responses the caveat exists for, so read it directly instead of the derived boolean.
  const coverageNotices = useLandContextStore((state) => state.resultMeta?.coverageNotices ?? []);
  const partialCoverage = coverageNotices.some((notice) => notice.coverageState === "partial_area_coverage");
  const selection = useLandContextStore((state) => state.selection);
  const queryStatus = useLandContextStore((state) => state.queryStatus);

  if (!data) {
    if (budgetExceeded) {
      return (
        <div className="p-4 text-xs text-amber-400" role="status">
          {budgetExceededMessage(budgetExceeded)}
        </div>
      );
    }
    if (partialCoverage) {
      return (
        <div className="p-4 text-xs text-zinc-400" role="status">
          This area only has partial source coverage -- some results may be missing.
        </div>
      );
    }
    // Say what is actually true for this moment: no selection yet, a lookup pending or in
    // flight (the store sets `idle` with a selection until the controller flips it), or a
    // selection whose candidates are listed but none focused. The old fixed sentence read
    // "select a point" right after the user had selected one.
    const emptyMessage =
      selection === null
        ? "Click a point on the map, or pick a bounded project area, to see place details and public routes."
        : queryStatus !== "settled" && queryStatus !== "error"
          ? "Looking up what governs the selected place…"
          : typeof resultsCount === "number" && resultsCount > 0
            ? "Pick one of the listed candidates to see its details and public routes."
            : "No admitted source answered for the selected place. The map notice states each family's gap.";
    return (
      <div className="p-4 text-xs text-zinc-500" role="status">
        {emptyMessage}
      </div>
    );
  }

  const primaryRelationship = data.officeCards[0]?.relationships[0];
  const primaryOffice = data.officeCards[0]?.office;
  const showCandidateBrowser =
    typeof resultsCount === "number" &&
    resultsCount > 1 &&
    onFocusPreviousCandidate &&
    onFocusNextCandidate;

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

      {showCandidateBrowser ? (
        <nav
          className="flex items-center justify-between gap-2 rounded-lg border border-zinc-700 bg-zinc-900 p-2"
          aria-label="Overlapping features"
        >
          <button
            type="button"
            onClick={onFocusPreviousCandidate}
            className="min-h-11 min-w-11 rounded border border-zinc-700 px-2 py-1.5 text-xs font-medium text-zinc-300 hover:bg-zinc-800 hover:text-zinc-100"
          >
            Previous
          </button>
          <span aria-live="polite" className="text-xs text-zinc-400">
            Feature {candidateIndex !== null ? candidateIndex + 1 : "–"} of {resultsCount}
          </span>
          <button
            type="button"
            onClick={onFocusNextCandidate}
            className="min-h-11 min-w-11 rounded border border-zinc-700 px-2 py-1.5 text-xs font-medium text-zinc-300 hover:bg-zinc-800 hover:text-zinc-100"
          >
            Next
          </button>
        </nav>
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
