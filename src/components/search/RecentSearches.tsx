"use client";

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

export default function RecentSearches() {
  const { recentSearches, clearRecentSearches, addRecentSearch, reset } = useSearchStore();
  const map = useMap();

  if (recentSearches.length === 0) return null;

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

  return (
    <div className="border-t border-[hsl(var(--border))]">
      <div className="flex items-center justify-between px-3 py-2">
        <span className="text-xs font-medium text-[hsl(var(--muted-foreground))] uppercase tracking-wide">
          Recent
        </span>
        <button
          onClick={clearRecentSearches}
          className="text-xs text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--foreground))] transition-colors"
        >
          Clear
        </button>
      </div>
      <ul className="max-h-[300px] overflow-y-auto" role="listbox">
        {recentSearches.map((result) => (
          <li
            key={result.id}
            role="option"
            aria-selected={false}
            className={cn(
              "flex items-center gap-2.5 px-3 py-2.5 cursor-pointer",
              "hover:bg-[hsl(var(--accent))] transition-colors"
            )}
            onClick={() => flyTo(result)}
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
    </div>
  );
}
