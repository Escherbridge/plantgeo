"use client";

import * as React from "react";
import { Search, X, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { useSearchStore } from "@/stores/search-store";
import { useGeocode } from "@/hooks/useGeocode";
import { useMapStore } from "@/stores/map-store";
import SearchResults from "./SearchResults";
import RecentSearches from "./RecentSearches";

export default function SearchBar() {
  const { query, isOpen, isLoading, setQuery, setResults, setIsOpen, setIsLoading, setSelectedIndex, reset } =
    useSearchStore();
  const viewport = useMapStore((s) => s.viewport);
  const inputRef = React.useRef<HTMLInputElement>(null);
  const [isExpanded, setIsExpanded] = React.useState(false);

  const { results, isLoading: geocodeLoading } = useGeocode(query, {
    lat: viewport.latitude,
    lon: viewport.longitude,
  });

  React.useEffect(() => {
    setResults(results);
  }, [results, setResults]);

  React.useEffect(() => {
    setIsLoading(geocodeLoading);
  }, [geocodeLoading, setIsLoading]);

  function handleInputChange(e: React.ChangeEvent<HTMLInputElement>) {
    setQuery(e.target.value);
    setIsOpen(true);
    setSelectedIndex(0);
  }

  function handleClear() {
    reset();
    inputRef.current?.focus();
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLDivElement>) {
    if (e.key === "Escape") {
      setIsOpen(false);
      inputRef.current?.blur();
    }
  }

  function handleExpandToggle() {
    setIsExpanded(true);
    setIsOpen(true);
    setTimeout(() => inputRef.current?.focus(), 0);
  }

  const showResults = isOpen && query.length >= 2;
  const showRecent = isOpen && query.length === 0;

  return (
    <>
      <button
        className={cn(
          "absolute top-4 left-4 z-10 sm:hidden",
          "flex h-10 w-10 items-center justify-center rounded-full",
          "bg-[var(--glass-bg)] border border-[var(--glass-border)]",
          "shadow-[var(--shadow-lg)] [backdrop-filter:blur(var(--glass-blur))]",
          "text-[hsl(var(--foreground))]",
          isExpanded && "hidden"
        )}
        onClick={handleExpandToggle}
        aria-label="Open search"
      >
        <Search className="size-5" />
      </button>

      <div
        className={cn(
          "absolute top-4 left-4 z-10 w-[min(340px,calc(100vw-2rem))]",
          "bg-[var(--glass-bg)] border border-[var(--glass-border)]",
          "shadow-[var(--shadow-lg)] [backdrop-filter:blur(var(--glass-blur))]",
          "rounded-xl overflow-hidden",
          !isExpanded ? "hidden sm:block" : "block"
        )}
        onKeyDown={handleKeyDown}
      >
        <div className="flex items-center gap-2 px-3 py-2.5">
          {isLoading ? (
            <Loader2 className="size-4 shrink-0 text-[hsl(var(--muted-foreground))] animate-spin" />
          ) : (
            <Search className="size-4 shrink-0 text-[hsl(var(--muted-foreground))]" />
          )}
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={handleInputChange}
            onFocus={() => setIsOpen(true)}
            placeholder="Search places..."
            className={cn(
              "flex-1 bg-transparent text-sm text-[hsl(var(--foreground))]",
              "placeholder:text-[hsl(var(--muted-foreground))]",
              "outline-none border-none"
            )}
            autoComplete="off"
            spellCheck={false}
          />
          {query.length > 0 && (
            <button
              onClick={handleClear}
              className="text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--foreground))] transition-colors"
              aria-label="Clear search"
            >
              <X className="size-4" />
            </button>
          )}
          <button
            className="sm:hidden text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--foreground))] transition-colors"
            onClick={() => {
              setIsExpanded(false);
              reset();
            }}
            aria-label="Close search"
          >
            <X className="size-4" />
          </button>
        </div>

        {showResults && <SearchResults />}
        {showRecent && <RecentSearches />}
      </div>
    </>
  );
}
