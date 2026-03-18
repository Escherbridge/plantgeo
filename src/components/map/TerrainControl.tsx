"use client";

import { Mountain } from "lucide-react";
import { useMapStore } from "@/stores/map-store";
import { Button } from "@/components/ui/button";
import { Slider } from "@/components/ui/slider";

export default function TerrainControl() {
  const { isTerrainEnabled, terrainExaggeration, toggleTerrain, setTerrainExaggeration } =
    useMapStore();

  return (
    <div className="flex flex-col gap-2">
      <Button
        variant={isTerrainEnabled ? "default" : "ghost"}
        size="icon"
        onClick={toggleTerrain}
        title="Toggle terrain"
      >
        <Mountain />
      </Button>
      {isTerrainEnabled && (
        <div className="flex w-28 flex-col gap-1 px-1">
          <Slider
            min={0}
            max={3}
            step={0.1}
            value={terrainExaggeration}
            onValueChange={setTerrainExaggeration}
          />
          <span className="text-center text-xs text-[hsl(var(--muted-foreground))]">
            {terrainExaggeration.toFixed(1)}x
          </span>
        </div>
      )}
    </div>
  );
}
