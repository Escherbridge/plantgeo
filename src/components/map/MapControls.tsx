"use client";

import { useEffect } from "react";
import { Box, Building2 } from "lucide-react";
import { useMapStore } from "@/stores/map-store";
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
  const activeLayers = useMapStore((s) => s.activeLayers);
  const toggleLayer = useMapStore((s) => s.toggleLayer);
  const buildingFootprintsEnabled = activeLayers.includes("building-footprints");

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
      className="left-4 translate-x-0 max-w-[calc(100vw-2rem)] overflow-x-auto"
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
          onClick={toggle3D}
          title="Toggle 3D"
        >
          <Box />
        </Button>
        <Button
          variant={buildingFootprintsEnabled ? "default" : "ghost"}
          size="icon"
          onClick={() => toggleLayer("building-footprints")}
          title="Toggle 3D building footprints"
        >
          <Building2 />
        </Button>
      </div>
    </FloatingToolbar>
  );
}
