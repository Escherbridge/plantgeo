"use client";

import { useEffect } from "react";
import { Box, Building2 } from "lucide-react";
import { useMapStore } from "@/stores/map-store";
import { useLayerToggle, useToggleLayer } from "@/lib/map/layer-toggle-context";
import { Button } from "@/components/ui/button";
import { FloatingToolbar } from "@/components/ui/floating-toolbar";
import TerrainControl from "./TerrainControl";
import GlobeToggle from "./GlobeToggle";
import StyleSwitcher from "./StyleSwitcher";
import { AlertBell } from "@/components/ui/AlertBell";

export default function MapControls() {
  // Per-field selectors — MapControls reads no viewport field, so a whole-store
  // destructure would re-render this toolbar on every pan/zoom tick.
  const is3DEnabled = useMapStore((s) => s.is3DEnabled);
  const toggle3D = useMapStore((s) => s.toggle3D);
  const toggleTerrain = useMapStore((s) => s.toggleTerrain);
  const toggleGlobe = useMapStore((s) => s.toggleGlobe);
  const setCurrentStyle = useMapStore((s) => s.setCurrentStyle);
  const resetView = useMapStore((s) => s.resetView);
  const toggleLayer = useToggleLayer();
  // Subscribes to this one layer, not the whole toggle list.
  const buildingFootprintsEnabled = useLayerToggle("building-footprints");

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (
        e.target instanceof HTMLInputElement ||
        e.target instanceof HTMLTextAreaElement
      )
        return;

      switch (e.key.toLowerCase()) {
        case "r":
          resetView();
          break;
        case "t":
          toggleTerrain();
          break;
        case "g":
          toggleGlobe();
          break;
        case "1":
          setCurrentStyle("dark");
          break;
        case "2":
          setCurrentStyle("light");
          break;
        case "3":
          setCurrentStyle("satellite");
          break;
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [resetView, toggleTerrain, toggleGlobe, setCurrentStyle]);

  return (
    <FloatingToolbar
      position="bottom"
      // The edge fade is the scroll affordance: on a phone this toolbar CAN overflow
      // sideways, and without the fade the controls past the fold simply do not exist
      // as far as the user can tell. Desktop fits, so the mask stays mobile-only --
      // a permanent fade on a non-scrolling toolbar reads as a rendering bug.
      className="left-4 translate-x-0 max-w-[calc(100vw-2rem)] overflow-x-auto max-sm:mask-[linear-gradient(to_right,transparent,black_12px,black_calc(100%-12px),transparent)]"
    >
      <div className="flex items-center gap-2">
        <StyleSwitcher />
        <div className="h-6 w-px bg-[hsl(var(--border))]" />
        <TerrainControl />
        <div className="h-6 w-px bg-[hsl(var(--border))]" />
        <GlobeToggle />
        <div className="h-6 w-px bg-[hsl(var(--border))]" />
        <AlertBell />
        <div className="h-6 w-px bg-[hsl(var(--border))]" />
        <Button
          variant={is3DEnabled ? "default" : "ghost"}
          size="icon"
          // size="icon" is 40px; grown to the 44px mobile minimum here rather than in
          // button.tsx, since that variant is shared well beyond this toolbar.
          className="max-sm:h-11 max-sm:w-11"
          onClick={toggle3D}
          title="Toggle 3D"
          aria-label="Toggle 3D"
          aria-pressed={is3DEnabled}
        >
          <Box />
        </Button>
        <Button
          variant={buildingFootprintsEnabled ? "default" : "ghost"}
          size="icon"
          className="max-sm:h-11 max-sm:w-11"
          onClick={() => toggleLayer("building-footprints")}
          title="Toggle 3D building footprints"
          aria-label="Toggle 3D building footprints"
          aria-pressed={buildingFootprintsEnabled}
        >
          <Building2 />
        </Button>
      </div>
    </FloatingToolbar>
  );
}
