import { create } from "zustand";
import { devtools, persist } from "zustand/middleware";
import type { NormalizedGeocodingResult } from "@/lib/geocoding";

/**
 * What a place search is, once the field stopped being three components.
 *
 * It held five more fields until 2026-08-09 -- `results`, `isOpen`, `isLoading` and
 * `selectedIndex`, plus their setters -- because the field, the live list and the recents list
 * were three components that could only talk through a store. `results` and `isLoading` were a
 * copy of what `useGeocode` already returns, kept in sync by two effects; `isOpen` described a
 * floating dropdown that no longer exists (a section is open when it is expanded); and
 * `selectedIndex` was the arrow-key cursor of one list, which is now local state in the one
 * component that draws it.
 *
 * What is left is the two facts that outlive a render: what was typed, and where the reader has
 * been. Only the second is persisted.
 */
interface SearchState {
  query: string;
  recentSearches: NormalizedGeocodingResult[];
  setQuery: (query: string) => void;
  addRecentSearch: (result: NormalizedGeocodingResult) => void;
  clearRecentSearches: () => void;
  /** Clears the field without forgetting anything: called after a result is flown to. */
  reset: () => void;
}

export const useSearchStore = create<SearchState>()(
  devtools(
    persist(
      (set) => ({
        query: "",
        recentSearches: [],

        setQuery: (query) => set({ query }),
        addRecentSearch: (result) =>
          set((s) => {
            const deduped = s.recentSearches.filter((r) => r.id !== result.id);
            return { recentSearches: [result, ...deduped].slice(0, 10) };
          }),
        clearRecentSearches: () => set({ recentSearches: [] }),
        reset: () => set({ query: "" }),
      }),
      {
        name: "plantgeo-search",
        partialize: (s) => ({ recentSearches: s.recentSearches }),
      }
    )
  )
);
