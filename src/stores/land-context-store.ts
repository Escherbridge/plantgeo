import { create } from "zustand";
import { devtools } from "zustand/middleware";
import type { CoverageState, LandContextResult } from "@/lib/environmental/land-context-contract";

/**
 * The four independently toggleable land-context groups. See
 * conductor/tracks/pnw_land_contact_experience_20260911/spec.md "Scope and four toggles".
 * Each group has its own source/version and admission status -- toggling one on never implies
 * the others are covered or that this UI decides admission.
 */
export type LandContextGroupId =
  | "parcels-land-use"
  | "electric-utility-territories"
  | "blm-lands"
  | "state-managed-lands";

export const LAND_CONTEXT_GROUP_IDS: readonly LandContextGroupId[] = [
  "parcels-land-use",
  "electric-utility-territories",
  "blm-lands",
  "state-managed-lands",
];

export const LAND_CONTEXT_GROUP_LABELS: Record<LandContextGroupId, string> = {
  "parcels-land-use": "Parcels & land use",
  "electric-utility-territories": "Electric utility territories",
  "blm-lands": "BLM lands",
  "state-managed-lands": "State-managed lands",
};

/**
 * How the current selection was made. Per spec "Selection uses a picked/searched point,
 * explicit parcel/tract or selected bounded project area. Never substitute the viewport
 * centre." -- there is deliberately no "viewport" variant here.
 */
export type LandContextSelectionMode = "point" | "parcel" | "area";

export interface LandContextSelectionInput {
  mode: LandContextSelectionMode;
  /** [lng, lat] for a picked/searched point. */
  point?: [number, number];
  /** Namespaced parcel/tract identifier for an explicit-parcel selection. */
  parcelId?: string;
  /** Closed-ring polygon (GeoJSON position array) for a user-drawn bounded project area. */
  areaPolygon?: GeoJSON.Position[];
}

/**
 * One intersecting feature returned for the current selection, already tagged with which
 * toggle group it belongs to so the panel/agent can render group-distinct cards without
 * re-deriving category from geometry. This is intentionally the UI-facing shape, not the
 * reference-plane's raw record -- the reader hook (`useLandContextQuery`, a placeholder here)
 * owns translating source records into this shape.
 */
export interface LandContextFeature {
  id: string;
  /** Original evidence retained for the detail panel and stable contact lookup. */
  evidence?: LandContextResult;
  group: LandContextGroupId;
  /** Short human label for hover card / list item, e.g. "Parcel 12-3456-789". */
  title: string;
  /** Category or type within the group, e.g. "Agricultural", "Municipal distribution". */
  category?: string;
  /** Source publication/version label, e.g. "Whatcom County Assessor, 2026 Q2". */
  sourceVintage?: string;
  /** One-line contact-route summary for the hover surface, per spec's "concise identity card". */
  contactRouteSummary?: string;
  geometry: GeoJSON.Geometry;
  /** Whether this office/contact route has been verified recently -- surfaced, not hidden. */
  contactVerified?: boolean;
}

/**
 * Explicit truncation/pagination metadata for an area selection. Per spec: "return the
 * intersecting features ... with stated truncation/pagination and coverage; do not reduce the
 * project to its centroid or arbitrarily choose the first overlap." A capped list is not a
 * complete project-area contact inventory, so `hasMore`/`totalCount` must always be shown
 * alongside the returned features, never silently dropped.
 */
export interface LandContextResultMeta {
  totalCount: number;
  returnedCount: number;
  hasMore: boolean;
  /** Opaque cursor for fetching the next page, when the reader hook supports it. */
  nextCursor?: string | null;
  /** True when the selection area only partially overlaps admitted source coverage. */
  partialCoverage?: boolean;
  /**
   * Populated only for a typed `{status: "budget_exceeded"}` reader response (see
   * `BudgetExceededResult` in `src/lib/server/services/land-context/types.ts`) -- distinct from
   * `partialCoverage`, which describes a genuine but incomplete source-coverage match. A
   * budget-exceeded selection never reaches the reader at all, so it must never be reported as
   * "partial coverage". `null`/absent means the current result is not budget-exceeded.
   */
  budgetExceeded?: {
    reason:
      | "aoi_area_exceeds_limit"
      | "geometry_vertices_exceed_limit"
      | "feature_count_would_exceed_limit"
      | "response_bytes_would_exceed_limit"
      | "outside_pilot_states";
    limit: number;
    requested: number | null;
  } | null;
  /**
   * Coverage statements the reader returned INSTEAD of features: every non-"matched" result's
   * typed state plus its verbatim `unresolvedGaps`. This is how "no source admitted for reads
   * yet" (the reader's own gap string) reaches the UI instead of being dropped with the result
   * it rode in on -- see `LandContextStatusNotice`. Empty/absent when every result matched.
   */
  coverageNotices?: { coverageState: CoverageState; gaps: string[] }[];
}

/**
 * Where the current selection's lookup stands. "idle" = nothing to ask (no selection or no
 * group on); "settled" = the reader answered, so an empty `results` is a real answer and not a
 * pending one. The notice component needs this to avoid claiming "no features" mid-flight.
 */
export type LandContextQueryStatus = "idle" | "loading" | "settled" | "error";

interface LandContextState {
  cropCoverEnabled: boolean;
  cropCoverReleaseDay: string | null;
  cropCoverLatestPublishedDay: string | null;
  setCropCoverLatestPublishedDay: (day: string | null) => void;
  setCropCoverEnabled: (enabled: boolean) => void;
  setCropCoverReleaseDay: (day: string | null) => void;
  /** Per-group on/off, independent of any other group. */
  enabledGroups: Record<LandContextGroupId, boolean>;
  toggleGroup: (group: LandContextGroupId) => void;
  setGroupEnabled: (group: LandContextGroupId, enabled: boolean) => void;

  /** The persistent selection -- survives hover changes and browsing between candidates. */
  selection: LandContextSelectionInput | null;
  setSelection: (selection: LandContextSelectionInput | null) => void;
  clearSelection: () => void;

  /** All intersecting features across all enabled groups for the current selection. */
  results: LandContextFeature[];
  resultMeta: LandContextResultMeta | null;
  queryStatus: LandContextQueryStatus;
  /** The controller is the only writer; `status` defaults to "settled" for callers that only carry data. */
  setResults: (
    results: LandContextFeature[],
    meta: LandContextResultMeta | null,
    status?: LandContextQueryStatus
  ) => void;

  /**
   * A "next candidate" cursor into `results`, not a single `selectedFeature`. Lets the user
   * browse every overlapping feature (boundary points, conflicting assignments) without losing
   * the selected area. `null` means "no candidate focused yet" -- distinct from index 0.
   */
  candidateIndex: number | null;
  setCandidateIndex: (index: number | null) => void;
  focusNextCandidate: () => void;
  focusPreviousCandidate: () => void;

  /**
   * Transient hover/keyboard-focus state for the dismissible identity card. Separate from the
   * pinned selection per spec: "A hover surface is dismissible ... essential information and
   * actions are also available in the pinned panel."
   */
  hoveredFeature: LandContextFeature | null;
  /** Screen-space anchor for the hover/focus identity card, in CSS pixels. Null for a
   * keyboard-focus-only hover where the trigger element positions the card via CSS instead. */
  hoverPosition: { x: number; y: number } | null;
  setHoveredFeature: (
    feature: LandContextFeature | null,
    position?: { x: number; y: number } | null
  ) => void;

  /** Whether the pinned detail panel is open. Distinct from having a selection, so dismissing
   * the panel (Escape / explicit close) doesn't discard the selection or results. */
  panelOpen: boolean;
  openPanel: () => void;
  closePanel: () => void;
}

const ALL_GROUPS_ENABLED: Record<LandContextGroupId, boolean> = {
  "parcels-land-use": false,
  "electric-utility-territories": false,
  "blm-lands": false,
  "state-managed-lands": false,
};

export const useLandContextStore = create<LandContextState>()(
  devtools((set, get) => ({
    cropCoverEnabled: false,
    cropCoverReleaseDay: null,
    cropCoverLatestPublishedDay: null,
    setCropCoverLatestPublishedDay: (day) => set((state) => state.cropCoverLatestPublishedDay === day
      ? state : { cropCoverLatestPublishedDay: day }),
    setCropCoverEnabled: (enabled) => set({ cropCoverEnabled: enabled }),
    setCropCoverReleaseDay: (day) => set({ cropCoverReleaseDay: day }),
    enabledGroups: { ...ALL_GROUPS_ENABLED },
    toggleGroup: (group) =>
      set((state) => ({
        enabledGroups: { ...state.enabledGroups, [group]: !state.enabledGroups[group] },
      })),
    setGroupEnabled: (group, enabled) =>
      set((state) => ({ enabledGroups: { ...state.enabledGroups, [group]: enabled } })),

    selection: null,
    setSelection: (selection) =>
      set({ selection, results: [], resultMeta: null, candidateIndex: null, queryStatus: "idle" }),
    clearSelection: () =>
      set({
        selection: null,
        results: [],
        resultMeta: null,
        candidateIndex: null,
        queryStatus: "idle",
        panelOpen: false,
      }),

    results: [],
    resultMeta: null,
    queryStatus: "idle",
    setResults: (results, meta, status = "settled") =>
      set({ results, resultMeta: meta, queryStatus: status }),

    candidateIndex: null,
    setCandidateIndex: (index) => set({ candidateIndex: index }),
    focusNextCandidate: () => {
      const { results, candidateIndex } = get();
      if (results.length === 0) return;
      const next = candidateIndex === null ? 0 : (candidateIndex + 1) % results.length;
      set({ candidateIndex: next });
    },
    focusPreviousCandidate: () => {
      const { results, candidateIndex } = get();
      if (results.length === 0) return;
      const prev =
        candidateIndex === null ? results.length - 1 : (candidateIndex - 1 + results.length) % results.length;
      set({ candidateIndex: prev });
    },

    hoveredFeature: null,
    hoverPosition: null,
    setHoveredFeature: (feature, position = null) =>
      set({ hoveredFeature: feature, hoverPosition: feature ? position : null }),

    panelOpen: false,
    openPanel: () => set({ panelOpen: true }),
    closePanel: () => set({ panelOpen: false }),
  }))
);
