"use client";

import { useEffect } from "react";
import { SEARCH_INPUT_DOM_ID } from "@/components/map/layer-panel/SearchDockSection";
import { useMapStore } from "@/stores/map-store";
import { usePanelStore } from "@/stores/panel-store";

/**
 * Every keyboard shortcut the map answers, in one always-mounted headless component.
 *
 * It exists because the surfaces that used to own these bindings are gone and the surface that
 * replaced them collapses. `MapControls` held r/t/g/1/2/3 and `CommandPalette` held Ctrl/Cmd+K;
 * both were mounted unconditionally, so both kept working with nothing open. Registering the
 * same keys inside a manager section would silently unbind them the moment that section
 * collapsed -- a collapsed section is an unmounted section, which is the whole point of the
 * manager's economics. This component renders `null` and never unmounts, exactly as
 * `TimeSliderCapabilitiesLoader` does for the same reason.
 *
 * Ctrl/Cmd+K is the one binding that changed meaning. It used to open a floating command
 * palette that duplicated the basemap, terrain, globe, 3D and fire-perimeter controls; it now
 * opens the manager at the Search section and puts the caret in the field, which is what the
 * shortcut always advertised itself as ("Ctrl K" sat inside the search field, not beside a
 * command list). The focus is deferred one frame because the section is unmounted while
 * collapsed: `focusDockSection` expands it, and the field exists a render later.
 */
export default function MapKeyboardShortcuts() {
  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        usePanelStore.getState().focusDockSection("search");
        requestAnimationFrame(() => {
          document.getElementById(SEARCH_INPUT_DOM_ID)?.focus();
        });
        return;
      }

      // A single letter typed into a field is text, never a command.
      if (
        event.target instanceof HTMLInputElement ||
        event.target instanceof HTMLTextAreaElement ||
        (event.target instanceof HTMLElement && event.target.isContentEditable)
      ) {
        return;
      }
      if (event.metaKey || event.ctrlKey || event.altKey) return;

      // Read imperatively rather than through selectors: this handler is registered once for
      // the life of the map, and listing the actions as dependencies would re-register it.
      const mapState = useMapStore.getState();
      switch (event.key.toLowerCase()) {
        case "r":
          mapState.resetView();
          break;
        case "t":
          mapState.toggleTerrain();
          break;
        case "g":
          mapState.toggleGlobe();
          break;
        case "1":
          mapState.setCurrentStyle("dark");
          break;
        case "2":
          mapState.setCurrentStyle("light");
          break;
        case "3":
          mapState.setCurrentStyle("satellite");
          break;
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, []);

  return null;
}
