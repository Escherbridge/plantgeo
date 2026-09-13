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
    <ul className="land-context-feature-list" aria-label="Intersecting land context features">
      {results.map((feature, index) => (
        <li key={feature.id}>
          <button
            type="button"
            aria-pressed={candidateIndex === index}
            className="land-context-feature-list-item"
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
            <span className="land-context-feature-list-title">{feature.title}</span>
            <span className="land-context-feature-list-group">
              {LAND_CONTEXT_GROUP_LABELS[feature.group]}
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}
