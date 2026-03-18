"use client";

import { useEffect } from "react";
import { Box } from "lucide-react";
import { useMapStore } from "@/stores/map-store";
import { Button } from "@/components/ui/button";
import {
  FloatingToolbar,
  FloatingToolbarItem,
} from "@/components/ui/floating-toolbar";
import TerrainControl from "./TerrainControl";
import GlobeToggle from "./GlobeToggle";
import StyleSwitcher from "./StyleSwitcher";

export default function MapControls() {
  const {
    is3DEnabled,
    toggle3D,
    toggleTerrain,
    toggleGlobe,
    setCurrentStyle,
    resetView,
  } = useMapStore();

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
    <FloatingToolbar position="top" className="right-4 left-auto translate-x-0">
      <div className="flex items-center gap-2">
        <StyleSwitcher />
        <div className="h-6 w-px bg-[hsl(var(--border))]" />
        <TerrainControl />
        <div className="h-6 w-px bg-[hsl(var(--border))]" />
        <GlobeToggle />
        <div className="h-6 w-px bg-[hsl(var(--border))]" />
        <Button
          variant={is3DEnabled ? "default" : "ghost"}
          size="icon"
          onClick={toggle3D}
          title="Toggle 3D"
        >
          <Box />
        </Button>
      </div>
    </FloatingToolbar>
  );
}
