"use client";

import {
  LAND_CONTEXT_GROUP_IDS,
  LAND_CONTEXT_GROUP_LABELS,
  useLandContextStore,
} from "@/stores/land-context-store";

/**
 * Four independent toggles, one per land-context group (spec "Scope and four toggles"). Each
 * checkbox is a real `<input type="checkbox">` with a `<label>` so it is reachable and
 * operable by keyboard/screen reader with no extra ARIA -- no custom pointer-only switch here.
 */
export function LandContextToggles() {
  const enabledGroups = useLandContextStore((state) => state.enabledGroups);
  const toggleGroup = useLandContextStore((state) => state.toggleGroup);

  return (
    <fieldset className="land-context-toggles">
      <legend>Land context layers</legend>
      {LAND_CONTEXT_GROUP_IDS.map((group) => {
        const inputId = `land-context-toggle-${group}`;
        return (
          <div key={group} className="land-context-toggle-row">
            <input
              id={inputId}
              type="checkbox"
              checked={enabledGroups[group]}
              onChange={() => toggleGroup(group)}
            />
            <label htmlFor={inputId}>{LAND_CONTEXT_GROUP_LABELS[group]}</label>
          </div>
        );
      })}
    </fieldset>
  );
}
