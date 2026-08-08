"use client";

import { useCallback, useId, useMemo, useRef, useState } from "react";
import {
  Building,
  Crosshair,
  Globe,
  Home,
  Loader2,
  MapPin,
  Navigation,
  Search,
  X,
} from "lucide-react";
import { ControlDockSection } from "@/components/map/layer-panel/ControlDockSection";
import { IngestionCoverageBadge } from "@/components/map/IngestionCoverageBadge";
import { useGeocode } from "@/hooks/useGeocode";
import { useMap } from "@/lib/map/map-context";
import { cn } from "@/lib/utils";
import type {
  GeocodingResultType as ResultType,
  NormalizedGeocodingResult,
} from "@/lib/geocoding";
import { useMapStore } from "@/stores/map-store";
import { useSearchStore } from "@/stores/search-store";

/** The Search section's own title. Named for what it finds, not for the control. */
export const SEARCH_SECTION_LABEL = "Search places";

/**
 * The DOM id of the section's text field, so the Ctrl/Cmd+K shortcut can focus it.
 *
 * A shortcut cannot hold a ref into a section that is unmounted while collapsed, and the two
 * facts it needs -- expand the section, then focus the field -- happen a render apart. An id is
 * the one handle that survives that gap; see `MapKeyboardShortcuts`.
 */
export const SEARCH_INPUT_DOM_ID = "map-manager-search-input";

/** How close a result of each kind is worth flying to. */
const ZOOM_BY_TYPE: Record<ResultType, number> = {
  house: 17,
  street: 15,
  city: 12,
  state: 8,
  country: 5,
  other: 14,
};

/** Where a coordinate pair typed straight into the field lands. */
const COORDINATE_JUMP_ZOOM = 14;

function ResultIcon({ type }: { type: ResultType }) {
  const iconClasses = "size-4 shrink-0 text-[hsl(var(--muted-foreground))]";
  switch (type) {
    case "house":
      return <Home className={iconClasses} />;
    case "street":
      return <Navigation className={iconClasses} />;
    case "city":
      return <Building className={iconClasses} />;
    case "state":
    case "country":
      return <Globe className={iconClasses} />;
    default:
      return <MapPin className={iconClasses} />;
  }
}

/**
 * A "lat, lon" pair typed into the field, or null when the text is a place name.
 *
 * This is the one thing the deleted command palette could do that geocoding cannot: Photon
 * answers names, not coordinates. Bounds are checked rather than assumed, because MapLibre
 * throws on a non-finite centre instead of ignoring it -- the same rule `focus-params.ts`
 * applies to a URL.
 */
export function parseCoordinatePair(text: string): { lat: number; lon: number } | null {
  const parts = text.trim().split(/[\s,]+/);
  if (parts.length !== 2) return null;
  const latitude = Number(parts[0]);
  const longitude = Number(parts[1]);
  if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) return null;
  if (Math.abs(latitude) > 90 || Math.abs(longitude) > 180) return null;
  return { lat: latitude, lon: longitude };
}

/**
 * Search, as a section of the manager.
 *
 * Until 2026-08-09 this was a floating glass card at `top-4 left-4`, which is exactly where the
 * manager's own header sits -- the two collided, and search was the app's only control that was
 * neither in the manager nor reachable from it. Three files became this one: `SearchBar` (the
 * field), `SearchResults` (the live list) and `RecentSearches` (the remembered list), the last
 * two of which were the same list rendered twice, each with its own `ZOOM_BY_TYPE`, its own
 * icon switch and its own `max-h-[300px] overflow-y-auto` -- the nested-scroller defect
 * `panel-scroll.ts` rule 2 names. There is one list here, told which rows to draw.
 *
 * `useGeocode` issues nothing below two characters, so a seeded-open section costs no request
 * until someone types.
 */
export function SearchDockSection() {
  return (
    <ControlDockSection id="search" label={SEARCH_SECTION_LABEL}>
      <SearchDockBody />
    </ControlDockSection>
  );
}

function SearchDockBody() {
  const map = useMap();
  const query = useSearchStore((state) => state.query);
  const setQuery = useSearchStore((state) => state.setQuery);
  const recentSearches = useSearchStore((state) => state.recentSearches);
  const addRecentSearch = useSearchStore((state) => state.addRecentSearch);
  const clearRecentSearches = useSearchStore((state) => state.clearRecentSearches);
  const resetSearch = useSearchStore((state) => state.reset);
  const viewport = useMapStore((state) => state.viewport);
  const inputRef = useRef<HTMLInputElement>(null);
  const listId = useId();
  const optionIdPrefix = useId();

  // Ephemeral and local on purpose: which row the arrow keys have landed on is a fact about
  // one open list in one component, and the store it used to live in made it a fact about the
  // whole app that nothing else could ever read.
  const [requestedIndex, setRequestedIndex] = useState(0);

  const { results, isLoading } = useGeocode(query, {
    lat: viewport.latitude,
    lon: viewport.longitude,
  });

  const coordinateTarget = useMemo(() => parseCoordinatePair(query), [query]);
  const isSearching = query.trim().length >= 2;
  // Recents are what the field shows before anything is typed; results replace them, never
  // stack under them -- two lists of places at once is two answers to one question.
  const rows = isSearching ? results : recentSearches;

  // Clamped on the way OUT rather than reset in an effect: results arrive asynchronously and a
  // shorter list must not leave the cursor pointing past its end, and an effect that reset the
  // index on every keystroke would render once with a stale highlight before correcting it.
  const highlightedIndex = requestedIndex < rows.length ? requestedIndex : 0;

  const flyToResult = useCallback(
    (result: NormalizedGeocodingResult) => {
      map?.flyTo({ center: result.coordinates, zoom: ZOOM_BY_TYPE[result.type] });
      addRecentSearch(result);
      resetSearch();
    },
    [map, addRecentSearch, resetSearch]
  );

  const flyToCoordinates = useCallback(() => {
    if (coordinateTarget === null) return;
    map?.flyTo({
      center: [coordinateTarget.lon, coordinateTarget.lat],
      zoom: COORDINATE_JUMP_ZOOM,
    });
    resetSearch();
  }, [coordinateTarget, map, resetSearch]);

  // Bound to the FIELD, not to the list. The list carried these keys through a `tabIndex={-1}`
  // handler that could only fire once it had focus, which it never has while someone is typing
  // into the field above it -- so arrow-key selection was unreachable in practice.
  function handleKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Escape") {
      resetSearch();
      return;
    }
    if (rows.length === 0) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setRequestedIndex((highlightedIndex + 1) % rows.length);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setRequestedIndex((highlightedIndex - 1 + rows.length) % rows.length);
    } else if (event.key === "Enter") {
      event.preventDefault();
      const result = rows[highlightedIndex];
      if (result !== undefined) flyToResult(result);
    }
  }

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center gap-1.5 rounded-md border border-[hsl(var(--border))] bg-[hsl(var(--background)/0.4)] px-2 py-1.5 focus-within:ring-2 focus-within:ring-[hsl(var(--ring))]">
        {isLoading ? (
          <Loader2
            aria-hidden="true"
            className="size-3.5 shrink-0 animate-spin text-[hsl(var(--muted-foreground))]"
          />
        ) : (
          <Search aria-hidden="true" className="size-3.5 shrink-0 text-[hsl(var(--muted-foreground))]" />
        )}
        <input
          ref={inputRef}
          id={SEARCH_INPUT_DOM_ID}
          type="text"
          role="combobox"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Place name or lat, lon"
          aria-label={SEARCH_SECTION_LABEL}
          aria-expanded={rows.length > 0}
          aria-controls={rows.length > 0 ? listId : undefined}
          aria-activedescendant={
            rows.length > 0 ? `${optionIdPrefix}-${highlightedIndex}` : undefined
          }
          autoComplete="off"
          spellCheck={false}
          className="min-w-0 flex-1 border-none bg-transparent text-xs text-[hsl(var(--foreground))] outline-none placeholder:text-[hsl(var(--muted-foreground))]"
        />
        {query.length > 0 && (
          <button
            type="button"
            onClick={() => {
              resetSearch();
              inputRef.current?.focus();
            }}
            aria-label="Clear search"
            className="shrink-0 rounded p-1 text-[hsl(var(--muted-foreground))] transition-colors hover:text-[hsl(var(--foreground))] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--ring))]"
          >
            <X aria-hidden="true" className="size-3.5" />
          </button>
        )}
      </div>

      {coordinateTarget !== null && (
        <button
          type="button"
          onClick={flyToCoordinates}
          className="flex min-h-8 items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs text-[hsl(var(--foreground))] transition-colors hover:bg-[hsl(var(--accent))] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--ring))] max-sm:min-h-11"
        >
          <Crosshair aria-hidden="true" className="size-4 shrink-0 text-[hsl(var(--muted-foreground))]" />
          <span className="truncate font-mono">
            Go to {coordinateTarget.lat.toFixed(4)}, {coordinateTarget.lon.toFixed(4)}
          </span>
        </button>
      )}

      {isSearching && isLoading && (
        <p className="px-2 py-1.5 text-xs text-[hsl(var(--muted-foreground))]">Searching…</p>
      )}

      {isSearching && !isLoading && results.length === 0 && coordinateTarget === null && (
        <p className="px-2 py-1.5 text-xs text-[hsl(var(--muted-foreground))]">No results found</p>
      )}

      {!isSearching && recentSearches.length > 0 && (
        <div className="flex items-center justify-between px-2">
          <span className="text-[10px] font-semibold uppercase tracking-wide text-[hsl(var(--muted-foreground))]">
            Recent
          </span>
          <button
            type="button"
            onClick={clearRecentSearches}
            className="rounded px-1 text-[10px] text-[hsl(var(--muted-foreground))] transition-colors hover:text-[hsl(var(--foreground))] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--ring))]"
          >
            Clear
          </button>
        </div>
      )}

      {rows.length > 0 && (
        // No `max-h-*` and no `overflow-*`: the manager's body is the one scroller, and both
        // lists this replaced capped themselves at 300px inside it.
        <ul id={listId} role="listbox" aria-label={SEARCH_SECTION_LABEL} className="flex flex-col">
          {rows.map((result, index) => (
            <li key={result.id} role="presentation">
              <button
                type="button"
                id={`${optionIdPrefix}-${index}`}
                role="option"
                aria-selected={index === highlightedIndex}
                onClick={() => flyToResult(result)}
                onMouseEnter={() => setRequestedIndex(index)}
                className={cn(
                  "flex w-full min-h-8 items-center gap-2 rounded-md px-2 py-1.5 text-left transition-colors max-sm:min-h-11",
                  index === highlightedIndex
                    ? "bg-[hsl(var(--accent))]"
                    : "hover:bg-[hsl(var(--accent))]"
                )}
              >
                <ResultIcon type={result.type} />
                <span className="flex min-w-0 flex-col">
                  <span className="truncate text-xs font-medium text-[hsl(var(--foreground))]">
                    {result.name}
                  </span>
                  <span className="truncate text-[10px] text-[hsl(var(--muted-foreground))]">
                    {result.displayName}
                  </span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}

      <IngestionCoverageBadge />
    </div>
  );
}
