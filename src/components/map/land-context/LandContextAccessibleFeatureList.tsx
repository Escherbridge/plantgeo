"use client";

import { LAND_CONTEXT_GROUP_LABELS, useLandContextStore } from "@/stores/land-context-store";

/**
 * The keyboard-equivalent textual feature list. Per spec ("Accessibility and bounded
 * delivery"): "Provide keyboard selection and equivalent textual feature/contact lists" and
 * ("No pointer-only interaction — every action reachable via hover must also be reachable via
 * keyboard focus + Enter/Space"). This list is the primary keyboard path into the same
 * `results` the deck.gl layer renders on the map: each button is focusable in document order,
 * shows a visible focus ring (native browser focus outline, not suppressed), and Enter/Space
 * pins the same selection a map click would.
 *
 * Also doubles as the day-one accessible substitute for pointer-drag area selection: this list
 * only ever reflects an already-established `selection` (point, parcel or area) from whatever
 * upstream control created it -- it does not itself draw an area. See the mobile/area-selection
 * note in `LandContextDetailPanel.tsx` for where that control belongs.
 */
export function LandContextAccessibleFeatureList() {
  const results = useLandContextStore((state) => state.results);
  const candidateIndex = useLandContextStore((state) => state.candidateIndex);
  const setCandidateIndex = useLandContextStore((state) => state.setCandidateIndex);
  const setHoveredFeature = useLandContextStore((state) => state.setHoveredFeature);
  const openPanel = useLandContextStore((state) => state.openPanel);

  if (results.length === 0) return null;

  return (
    <ul
      className="max-w-sm space-y-1 rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--background))]/95 p-2 text-sm shadow-lg backdrop-blur-sm"
      aria-label="Intersecting land context features"
    >
      {results.map((feature, index) => (
        <li key={feature.id}>
          <button
            type="button"
            aria-pressed={candidateIndex === index}
            className={`min-h-11 w-full rounded px-2 py-1.5 text-left hover:bg-[hsl(var(--muted))] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[hsl(var(--ring))] ${
              candidateIndex === index ? "bg-[hsl(var(--muted))]" : ""
            }`}
            onFocus={() => setHoveredFeature(feature, null)}
            onBlur={() => setHoveredFeature(null)}
            onClick={() => {
              setCandidateIndex(index);
              openPanel();
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                setCandidateIndex(index);
                openPanel();
              }
            }}
          >
            <span className="block font-medium text-[hsl(var(--foreground))]">{feature.title}</span>
            <span className="block text-xs text-[hsl(var(--muted-foreground))]">
              {LAND_CONTEXT_GROUP_LABELS[feature.group]}
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}
