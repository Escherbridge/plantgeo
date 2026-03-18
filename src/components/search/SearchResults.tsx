"use client";

import * as React from "react";
import { Home, Navigation, Building, Globe, MapPin } from "lucide-react";
import { cn } from "@/lib/utils";
import { useSearchStore } from "@/stores/search-store";
import { useMap } from "@/lib/map/map-context";
import type { NormalizedGeocodingResult, ResultType } from "@/lib/server/services/geocoding";

const ZOOM_BY_TYPE: Record<ResultType, number> = {
  house: 17,
  street: 15,
  city: 12,
  state: 8,
  country: 5,
  other: 14,
};

function ResultIcon({ type }: { type: ResultType }) {
  const cls = "size-4 shrink-0 text-[hsl(var(--muted-foreground))]";
  switch (type) {
    case "house":
      return <Home className={cls} />;
    case "street":
      return <Navigation className={cls} />;
    case "city":
      return <Building className={cls} />;
    case "state":
    case "country":
      return <Globe className={cls} />;
    default:
      return <MapPin className={cls} />;
  }
}

export default function SearchResults() {
  const { results, selectedIndex, isLoading, setSelectedIndex, addRecentSearch, reset } = useSearchStore();
  const map = useMap();

  function flyTo(result: NormalizedGeocodingResult) {
    if (map) {
      map.flyTo({
        center: result.coordinates,
        zoom: ZOOM_BY_TYPE[result.type],
      });
    }
    addRecentSearch(result);
    reset();
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLUListElement>) {
    if (results.length === 0) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setSelectedIndex((selectedIndex + 1) % results.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setSelectedIndex((selectedIndex - 1 + results.length) % results.length);
    } else if (e.key === "Enter") {
      e.preventDefault();
      const result = results[selectedIndex];
      if (result) flyTo(result);
    }
  }

  if (isLoading) {
    return (
      <div className="border-t border-[hsl(var(--border))] px-3 py-4 text-center text-sm text-[hsl(var(--muted-foreground))]">
        Searching...
      </div>
    );
  }

  if (results.length === 0) {
    return (
      <div className="border-t border-[hsl(var(--border))] px-3 py-4 text-center text-sm text-[hsl(var(--muted-foreground))]">
        No results found
      </div>
    );
  }

  return (
    <ul
      className="border-t border-[hsl(var(--border))] max-h-[300px] overflow-y-auto"
      onKeyDown={handleKeyDown}
      tabIndex={-1}
      role="listbox"
    >
      {results.map((result, index) => (
        <li
          key={result.id}
          role="option"
          aria-selected={index === selectedIndex}
          className={cn(
            "flex items-center gap-2.5 px-3 py-2.5 cursor-pointer",
            "hover:bg-[hsl(var(--accent))] transition-colors",
            index === selectedIndex && "bg-[hsl(var(--accent))]"
          )}
          onClick={() => flyTo(result)}
          onMouseEnter={() => setSelectedIndex(index)}
        >
          <ResultIcon type={result.type} />
          <div className="flex flex-col min-w-0">
            <span className="text-sm font-medium text-[hsl(var(--foreground))] truncate">
              {result.name}
            </span>
            <span className="text-xs text-[hsl(var(--muted-foreground))] truncate">
              {result.displayName}
            </span>
          </div>
        </li>
      ))}
    </ul>
  );
}
