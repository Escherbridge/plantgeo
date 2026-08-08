"use client";

import { useEffect, useState } from "react";
import { Layers, X } from "lucide-react";
import { MAP_MANAGER_DOCK_ID } from "@/components/map/layer-panel/LayerPanel";
import { useMapStore } from "@/stores/map-store";
import { usePanelStore } from "@/stores/panel-store";

/** Marks the hint as seen forever; opening the dock counts as having found it. */
const DOCK_HINT_DISMISSED_KEY = "plantgeo-rail-hint-dismissed";

/**
 * First-visit pointer at the dock. No data layer is on by default, so a new visitor faces a
 * blank map with nothing saying where the data lives. Shown once: dismissed by its own close
 * button or by opening the dock, and never again after that (localStorage). Starts hidden and
 * flips visible in an effect so the server render and the first client paint agree.
 *
 * The storage key still says "rail" and deliberately so -- it is the same hint, pointed at the
 * button that replaced the rail, and renaming it would show it again to everyone who has
 * already dismissed it.
 */
function DockHint() {
  const layerPanelOpen = usePanelStore((state) => state.layerPanelOpen);
  const [isVisible, setIsVisible] = useState(false);

  useEffect(() => {
    setIsVisible(localStorage.getItem(DOCK_HINT_DISMISSED_KEY) === null);
  }, []);

  useEffect(() => {
    if (layerPanelOpen && isVisible) {
      localStorage.setItem(DOCK_HINT_DISMISSED_KEY, "1");
      setIsVisible(false);
    }
  }, [layerPanelOpen, isVisible]);

  if (!isVisible) return null;

  return (
    <div
      className="absolute left-[calc(var(--layer-panel-inset,0px)+4.25rem)] top-20 z-10 flex items-center gap-1.5 rounded-xl border border-(--glass-border) bg-(--glass-bg) py-1.5 pl-3 pr-1.5 text-xs text-[hsl(var(--foreground))] shadow-(--shadow-lg) [backdrop-filter:blur(var(--glass-blur))]"
      data-testid="dock-hint"
    >
      Turn on data layers here
      <button
        type="button"
        aria-label="Dismiss"
        onClick={() => {
          localStorage.setItem(DOCK_HINT_DISMISSED_KEY, "1");
          setIsVisible(false);
        }}
        className="flex h-6 w-6 items-center justify-center rounded-md text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--foreground))] max-sm:h-11 max-sm:w-11"
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}

/**
 * The one button that opens the dock, and everything that is left of the seven-button icon
 * rail it replaced.
 *
 * The rail's other seven buttons each opened one sheet; the sheets are gone and every one of
 * their surfaces is a section of this dock, so a second button per section would be a shortcut
 * to a place the dock already shows. The emerald dot survives from the rail's per-panel badge,
 * now answering the one question a closed dock leaves open: is anything drawn?
 *
 * `--layer-panel-inset` (set on MapView's root) keeps it clear of the dock it opens, exactly
 * as it keeps the bottom-left toolbar clear.
 */
export function DockToggle() {
  const layerPanelOpen = usePanelStore((state) => state.layerPanelOpen);
  const toggleLayerPanel = usePanelStore((state) => state.toggleLayerPanel);
  const hasActiveLayers = useMapStore((state) => state.activeLayers.length > 0);

  return (
    <>
      <div className="absolute left-[calc(var(--layer-panel-inset,0px)+0.75rem)] top-20 z-10 pr-1 transition-[left] duration-200">
        <button
          type="button"
          title="Layers"
          // The dot is the visual "the map is drawing something" signal; the name change is
          // its non-visual equivalent.
          aria-label={hasActiveLayers ? "Layers (layers active)" : "Layers"}
          aria-expanded={layerPanelOpen}
          // The dock (see MAP_MANAGER_DOCK_ID) renders nothing while undocked, so naming it
          // here only while open avoids the same dangling-reference bug DetailsSection had.
          aria-controls={layerPanelOpen ? MAP_MANAGER_DOCK_ID : undefined}
          onClick={toggleLayerPanel}
          // h-11 w-11 (44px) is the tap-target floor the rest of the map chrome holds to;
          // shrink-0 keeps it from squashing toward its 16px icon on a short viewport.
          className={[
            "relative flex h-11 w-11 shrink-0 items-center justify-center rounded-md shadow-md transition-colors",
            "bg-[hsl(var(--background))] text-[hsl(var(--foreground))]",
            "hover:bg-[hsl(var(--accent))] hover:text-[hsl(var(--accent-foreground))]",
            layerPanelOpen ? "bg-[hsl(var(--primary)/0.1)] ring-2 ring-[hsl(var(--primary))]" : "",
          ].join(" ")}
        >
          <Layers className="h-4 w-4" />
          {hasActiveLayers && (
            <span className="absolute -right-0.5 -top-0.5 h-2.5 w-2.5 rounded-full bg-emerald-500 ring-2 ring-[hsl(var(--background))]" />
          )}
        </button>
      </div>
      <DockHint />
    </>
  );
}
