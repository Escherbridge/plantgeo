import { create } from "zustand";
import { devtools } from "zustand/middleware";
import type {
  BotanicalOccurrenceFeature,
  BotanicalSpatialQuality,
} from "@/lib/botanical-occurrences";

/**
 * `LayerManager` writes the raw camelCase `BotanicalOccurrencesQueryResult` union here
 * (`src/lib/server/services/botanical-occurrences-client.ts`), not the snake_case
 * `BotanicalOccurrenceResponse` this store previously declared -- that mismatch was masked by an
 * `as never` cast at the one call site rather than caught by the compiler. That server-only
 * module cannot be imported here (client/server boundary, `scripts/check-client-server-imports.mjs`),
 * so this is a small structural mirror of just the fields this store actually needs, not a
 * duplicate of the wire contract -- it never reads `features`/`cells`, only the metadata a status
 * line would show.
 */
export interface BotanicalLastResponse {
  state: "detail" | "aggregate" | "refused" | "unavailable";
  releaseSetId: string | null;
  publishedAt: string | null;
}

/**
 * The filters block owns `release_set_id` as required-but-unset (`null`), never an empty
 * string default, so "no release pinned" is a real state the panel and the layers can both
 * check rather than an empty-string fetch that would 400 against the service.
 */
export interface BotanicalOccurrenceFilters {
  release_set_id: string | null;
  taxon_concept_id: string;
  family: string;
  collection_key: string;
  event_start: string;
  event_end: string;
  spatial_quality: BotanicalSpatialQuality;
  /** Which collection-evidence measure the effort layer draws. Declared here so the filters
   * panel and the effort layer share one selection. */
  effort_measure: "record_count" | "event_estimate" | "collection_count";
}

const DEFAULT_FILTERS: BotanicalOccurrenceFilters = {
  release_set_id: null,
  taxon_concept_id: "",
  family: "",
  collection_key: "",
  event_start: "",
  event_end: "",
  spatial_quality: "confirmed",
  effort_measure: "record_count",
};

interface BotanicalOccurrenceState {
  filters: BotanicalOccurrenceFilters;
  /** The most recent response for the active viewport/zoom band, or null before any fetch. */
  lastResponse: BotanicalLastResponse | null;
  /** The specimen the details panel shows; null clears the panel. */
  selectedFeature: BotanicalOccurrenceFeature | null;
  setFilters: (patch: Partial<BotanicalOccurrenceFilters>) => void;
  setReleaseSetId: (releaseSetId: string) => void;
  setLastResponse: (response: BotanicalLastResponse | null) => void;
  setSelectedFeature: (feature: BotanicalOccurrenceFeature | null) => void;
  resetFilters: () => void;
}

export const useBotanicalOccurrenceStore = create<BotanicalOccurrenceState>()(
  devtools((set) => ({
    filters: DEFAULT_FILTERS,
    lastResponse: null,
    selectedFeature: null,
    setFilters: (patch) => set((state) => ({ filters: { ...state.filters, ...patch } })),
    setReleaseSetId: (releaseSetId) =>
      set((state) => ({
        filters: { ...state.filters, release_set_id: releaseSetId.length > 0 ? releaseSetId : null },
      })),
    setLastResponse: (response) => set({ lastResponse: response }),
    setSelectedFeature: (feature) => set({ selectedFeature: feature }),
    resetFilters: () => set({ filters: DEFAULT_FILTERS, selectedFeature: null }),
  }))
);
