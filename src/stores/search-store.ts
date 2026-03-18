import { create } from "zustand";
import { devtools, persist } from "zustand/middleware";
import type { NormalizedGeocodingResult } from "@/lib/server/services/geocoding";

interface SearchState {
  query: string;
  results: NormalizedGeocodingResult[];
  selectedIndex: number;
  isOpen: boolean;
  isLoading: boolean;
  recentSearches: NormalizedGeocodingResult[];
  setQuery: (query: string) => void;
  setResults: (results: NormalizedGeocodingResult[]) => void;
  setSelectedIndex: (index: number) => void;
  setIsOpen: (open: boolean) => void;
  setIsLoading: (loading: boolean) => void;
  addRecentSearch: (result: NormalizedGeocodingResult) => void;
  clearRecentSearches: () => void;
  reset: () => void;
}

export const useSearchStore = create<SearchState>()(
  devtools(
    persist(
      (set) => ({
        query: "",
        results: [],
        selectedIndex: -1,
        isOpen: false,
        isLoading: false,
        recentSearches: [],

        setQuery: (query) => set({ query }),
        setResults: (results) => set({ results }),
        setSelectedIndex: (index) => set({ selectedIndex: index }),
        setIsOpen: (open) => set({ isOpen: open }),
        setIsLoading: (loading) => set({ isLoading: loading }),
        addRecentSearch: (result) =>
          set((s) => {
            const deduped = s.recentSearches.filter((r) => r.id !== result.id);
            return { recentSearches: [result, ...deduped].slice(0, 10) };
          }),
        clearRecentSearches: () => set({ recentSearches: [] }),
        reset: () =>
          set({ query: "", results: [], selectedIndex: -1, isOpen: false }),
      }),
      {
        name: "plantgeo-search",
        partialize: (s) => ({ recentSearches: s.recentSearches }),
      }
    )
  )
);
