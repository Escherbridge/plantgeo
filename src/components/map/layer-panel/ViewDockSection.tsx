"use client";

import { Box, Globe, Mountain, type LucideIcon } from "lucide-react";
import { ControlDockSection } from "@/components/map/layer-panel/ControlDockSection";
import StyleSwitcher from "@/components/map/StyleSwitcher";
import { Slider } from "@/components/ui/slider";
import { useMapStore } from "@/stores/map-store";

/** The View section's own title. Named for what it changes: how the map is drawn, not what. */
export const VIEW_SECTION_LABEL = "Map view";

/**
 * One render-mode switch: an icon, a name, and a track.
 *
 * The whole row is the button, so the visible name IS the accessible name -- a separate
 * `aria-label` beside a visible label is two names for one control, and they drift.
 */
function RenderModeRow({
  icon: Icon,
  label,
  isOn,
  onToggle,
}: {
  icon: LucideIcon;
  label: string;
  isOn: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={isOn}
      onClick={onToggle}
      // 44px on a phone, the tap-target floor every control on this map holds to.
      className="flex min-h-8 w-full items-center justify-between gap-2 rounded-md px-1 py-1 text-left text-xs text-[hsl(var(--foreground))] transition-colors hover:bg-[hsl(var(--muted)/0.4)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--ring))] max-sm:min-h-11"
    >
      <span className="flex min-w-0 items-center gap-1.5">
        <Icon aria-hidden="true" className="h-3.5 w-3.5 shrink-0 opacity-70" />
        <span className="truncate">{label}</span>
      </span>
      <span
        aria-hidden="true"
        className={[
          "flex h-4 w-7 shrink-0 items-center rounded-full p-0.5 transition-colors",
          isOn ? "bg-emerald-500" : "bg-[hsl(var(--border))]",
        ].join(" ")}
      >
        <span
          className={[
            "block h-3 w-3 rounded-full bg-white transition-transform",
            isOn ? "translate-x-3" : "translate-x-0",
          ].join(" ")}
        />
      </span>
    </button>
  );
}

/**
 * Render mode, as a section of the manager: basemap, terrain, projection, tilt.
 *
 * These four were a floating bar across the bottom of the canvas until 2026-08-09
 * (`MapControls`, with `TerrainControl` and `GlobeToggle` inside it). Two things were wrong
 * with that, and only one of them was that it was a second surface. The other was its fifth
 * button: a 3D-footprints switch writing the same `activeLayers` entry the layer tree's own
 * row wrote, one of them always out of sight. That button is not here. **Nothing in this
 * section touches `activeLayers`** -- changing how the map is lit must never add or drop a
 * layer, which is the rule in src/components/map/AGENTS.md, and keeping render mode in a
 * section that owns no layer switch is what makes that structural instead of remembered.
 */
export function ViewDockSection() {
  // Per-field selectors: this section reads no viewport field, so a whole-store destructure
  // would re-render it on every pan and zoom tick.
  const isTerrainEnabled = useMapStore((state) => state.isTerrainEnabled);
  const terrainExaggeration = useMapStore((state) => state.terrainExaggeration);
  const setTerrainExaggeration = useMapStore((state) => state.setTerrainExaggeration);
  const toggleTerrain = useMapStore((state) => state.toggleTerrain);
  const isGlobeView = useMapStore((state) => state.isGlobeView);
  const toggleGlobe = useMapStore((state) => state.toggleGlobe);
  const is3DEnabled = useMapStore((state) => state.is3DEnabled);
  const toggle3D = useMapStore((state) => state.toggle3D);

  return (
    <ControlDockSection id="view" label={VIEW_SECTION_LABEL}>
      <div className="flex flex-col gap-1">
        <div className="flex items-center justify-between gap-2 px-1 py-1">
          <span className="text-xs text-[hsl(var(--foreground))]">Basemap</span>
          <StyleSwitcher />
        </div>

        <RenderModeRow
          icon={Mountain}
          label="Terrain"
          isOn={isTerrainEnabled}
          onToggle={toggleTerrain}
        />
        {isTerrainEnabled && (
          <div className="flex flex-col gap-1 px-1 pb-1 pl-6">
            {/* The name and the spoken value are separate: the caption reads "1.5x", which is
                the value, not what the control is. */}
            <Slider
              min={0}
              max={3}
              step={0.1}
              value={terrainExaggeration}
              onValueChange={setTerrainExaggeration}
              aria-label="Terrain exaggeration"
              aria-valuetext={`${terrainExaggeration.toFixed(1)} times`}
            />
            <span className="text-[10px] text-[hsl(var(--muted-foreground))]">
              Exaggeration {terrainExaggeration.toFixed(1)}x
            </span>
          </div>
        )}

        <RenderModeRow icon={Globe} label="Globe" isOn={isGlobeView} onToggle={toggleGlobe} />
        <RenderModeRow icon={Box} label="3D tilt" isOn={is3DEnabled} onToggle={toggle3D} />
      </div>
    </ControlDockSection>
  );
}
