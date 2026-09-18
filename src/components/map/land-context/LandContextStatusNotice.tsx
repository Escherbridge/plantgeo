"use client";

import { useMemo } from "react";
import {
  LAND_CONTEXT_GROUP_IDS,
  LAND_CONTEXT_GROUP_LABELS,
  useLandContextStore,
  type LandContextFeature,
  type LandContextGroupId,
  type LandContextQueryStatus,
  type LandContextResultMeta,
  type LandContextSelectionInput,
} from "@/stores/land-context-store";
import type { CoverageState } from "@/lib/environmental/land-context-contract";

/**
 * The honest state of the land-context toggles, said out loud.
 *
 * A lit toggle beside an empty canvas reads as "nothing here". For this
 * plane that is false three different ways, and the reader already
 * distinguishes them -- it just had no surface: (1) nothing is drawn until
 * a point is clicked, because selection is click-driven by spec (never the
 * viewport centre); (2) a family may have NO admitted source yet, which the
 * reader states verbatim in `unresolvedGaps` ("no Parquet lane wired in
 * yet; reference plane not yet admitted for reads"); (3) a family may
 * simply have no feature at the clicked point. `deriveLandContextNotices`
 * tells those apart per family from the typed `coverageState` the reader
 * returned in place of features, and quotes the reader's gap strings rather
 * than paraphrasing them.
 *
 * Shape and styling follow `LayerManager`'s `parquetLayerFaults` stack
 * (`layerId`/`tone`/`message`, the same pill classes) so the array can be
 * spliced into that stack unchanged; the overlay variant anchors
 * bottom-centre only so it never paints over that stack's `top-12` slot.
 */
export interface LandContextNotice {
  /** Stable key and `data-testid` suffix (`land-context-notice-<layerId>`); named to match LayerManager's entries. */
  layerId: string;
  /** `fault` = the lookup itself failed; `notice` = a true statement about what is (not) drawn. */
  tone: "notice" | "fault";
  message: string;
}

export interface LandContextNoticeInput {
  enabledGroups: Record<LandContextGroupId, boolean>;
  selection: LandContextSelectionInput | null;
  results: LandContextFeature[];
  resultMeta: LandContextResultMeta | null;
  queryStatus: LandContextQueryStatus;
}

function labelList(groups: LandContextGroupId[]): string {
  return groups.map((group) => LAND_CONTEXT_GROUP_LABELS[group]).join(", ");
}

function describeSelection(selection: LandContextSelectionInput): string {
  if (selection.mode === "area") return "the selected area";
  if (selection.mode === "parcel") return "the selected parcel";
  return "the selected point";
}

function statedGaps(gaps: string[]): string {
  const unique = Array.from(new Set(gaps.filter((gap) => gap.trim().length > 0)));
  return unique.length > 0 ? ` Stated gap: ${unique.join("; ")}.` : "";
}

function budgetMessage(
  families: string,
  budgetExceeded: NonNullable<LandContextResultMeta["budgetExceeded"]>
): string {
  const reason: Record<typeof budgetExceeded.reason, string> = {
    aoi_area_exceeds_limit: "the selected area exceeds the read budget",
    geometry_vertices_exceed_limit: "the selected area's shape is too detailed to process",
    feature_count_would_exceed_limit: "too many features would be returned at once",
    response_bytes_would_exceed_limit: "the response would be too large to load at once",
    outside_pilot_states: "the selection is outside the pilot states (Washington, Oregon, Idaho)",
  };
  return `${families} were not looked up: ${reason[budgetExceeded.reason]}. Nothing is drawn.`;
}

function coverageMessage(
  coverageState: CoverageState,
  families: string,
  where: string,
  gaps: string[]
): string {
  switch (coverageState) {
    case "unknown_coverage":
      return `Coverage for ${families} at ${where} is unknown: no admitted source answered, so nothing is drawn.${statedGaps(gaps)}`;
    case "no_match_in_proven_coverage":
      return `No ${families} features at ${where}. Coverage here is proven, so this is a real absence for these groups.${statedGaps(gaps)}`;
    case "partial_area_coverage":
      return `${families}: the selected area is only partly covered by admitted sources and returned no features for these groups.${statedGaps(gaps)}`;
    case "outside_pilot":
      return `${where.charAt(0).toUpperCase()}${where.slice(1)} is outside the pilot states (Washington, Oregon, Idaho); ${families} are not looked up there.${statedGaps(gaps)}`;
    case "unavailable_history":
      return `${families}: history for the selected day is unavailable; only the current reference can be shown.${statedGaps(gaps)}`;
    case "matched":
      // Never reaches here (matched results become features), kept for exhaustiveness.
      return `${families} matched at ${where}.`;
  }
}

/** Pure derivation, exported for tests and for splicing into LayerManager's notice stack. */
export function deriveLandContextNotices(input: LandContextNoticeInput): LandContextNotice[] {
  const enabled = LAND_CONTEXT_GROUP_IDS.filter((group) => input.enabledGroups[group]);
  if (enabled.length === 0) return [];

  if (!input.selection) {
    return [
      {
        layerId: "land-context-select-point",
        tone: "notice",
        message:
          `${labelList(enabled)}: click a point on the map to look it up. ` +
          "Land context is looked up at a clicked point or a selected area, never the current view, " +
          "and nothing is drawn until you select one.",
      },
    ];
  }

  // Nothing to say mid-flight: an empty `results` is not yet an answer.
  if (input.queryStatus === "idle" || input.queryStatus === "loading") return [];

  if (input.queryStatus === "error") {
    return [
      {
        layerId: "land-context-request-failed",
        tone: "fault",
        message: `The land-context lookup for ${labelList(enabled)} failed before returning a state. No fallback is shown.`,
      },
    ];
  }

  const meta = input.resultMeta;
  if (meta?.budgetExceeded) {
    return [
      {
        layerId: "land-context-budget-exceeded",
        tone: "notice",
        message: budgetMessage(labelList(enabled), meta.budgetExceeded),
      },
    ];
  }

  // A family that came back with at least one feature needs no notice; the rest are told apart
  // by the typed coverage state the reader returned in place of features.
  const unanswered = enabled.filter((group) => !input.results.some((feature) => feature.group === group));
  if (unanswered.length === 0) return [];

  const families = labelList(unanswered);
  const where = describeSelection(input.selection);
  const coverage = meta?.coverageNotices ?? [];

  if (coverage.length === 0) {
    // Other families matched here, so the reader answered for this place; it returned nothing
    // for these families and made no statement about their coverage -- say exactly that.
    return [
      {
        layerId: "land-context-no-features",
        tone: "notice",
        message: `No ${families} features were returned at ${where}. Source coverage for them was not stated.`,
      },
    ];
  }

  // One line per distinct coverage state, gaps merged, so two identical statements never stack.
  const gapsByState = new Map<CoverageState, string[]>();
  for (const entry of coverage) {
    gapsByState.set(entry.coverageState, [...(gapsByState.get(entry.coverageState) ?? []), ...entry.gaps]);
  }
  return Array.from(gapsByState.entries()).map(([coverageState, gaps]) => ({
    layerId: `land-context-coverage-${coverageState}`,
    tone: "notice" as const,
    message: coverageMessage(coverageState, families, where, gaps),
  }));
}

// Identical pill classes to LayerManager's fault/notice stack; only the anchor differs.
const FAULT_CLASS =
  "rounded-md border border-red-500/40 bg-[hsl(var(--card))]/95 px-3 py-1.5 text-xs font-medium text-red-600 shadow-sm backdrop-blur dark:text-red-400";
const NOTICE_CLASS =
  "rounded-md border border-amber-500/40 bg-[hsl(var(--card))]/95 px-3 py-1.5 text-xs font-medium text-amber-700 shadow-sm backdrop-blur dark:text-amber-400";
const OVERLAY_CLASS =
  "pointer-events-none absolute bottom-6 left-1/2 z-20 flex max-w-[min(40rem,calc(100vw-2rem))] -translate-x-1/2 flex-col gap-1.5";

interface LandContextStatusNoticeProps {
  /** `overlay` (default) floats over the canvas; `inline` flows in a dock section. */
  variant?: "overlay" | "inline";
}

export function LandContextStatusNotice({ variant = "overlay" }: LandContextStatusNoticeProps) {
  const enabledGroups = useLandContextStore((state) => state.enabledGroups);
  const selection = useLandContextStore((state) => state.selection);
  const results = useLandContextStore((state) => state.results);
  const resultMeta = useLandContextStore((state) => state.resultMeta);
  const queryStatus = useLandContextStore((state) => state.queryStatus);

  const notices = useMemo(
    () => deriveLandContextNotices({ enabledGroups, selection, results, resultMeta, queryStatus }),
    [enabledGroups, selection, results, resultMeta, queryStatus]
  );

  if (notices.length === 0) return null;

  return (
    // No `aria-live` on the container: each child is already a live region (`status`/`alert`),
    // and wrapping those in a second one double-announces on some screen readers.
    <div
      className={variant === "overlay" ? OVERLAY_CLASS : "flex flex-col gap-1.5"}
      data-testid="land-context-notices"
    >
      {notices.map((notice) => (
        <p
          key={notice.layerId}
          role={notice.tone === "fault" ? "alert" : "status"}
          className={notice.tone === "fault" ? FAULT_CLASS : NOTICE_CLASS}
          data-testid={`land-context-notice-${notice.layerId}`}
        >
          {notice.message}
        </p>
      ))}
    </div>
  );
}
