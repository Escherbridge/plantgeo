"use client";

import { useEffect, useState } from "react";
import { Layers, X } from "lucide-react";
import { LayerLegend } from "@/components/map/layer-panel/LayerLegend";
import { useMapStore } from "@/stores/map-store";
import { usePanelStore } from "@/stores/panel-store";

/** Marks the hint as seen forever; opening the manager counts as having found it. */
const MANAGER_HINT_DISMISSED_KEY = "plantgeo-rail-hint-dismissed";

/**
 * First-visit pointer at the manager. No data layer is on by default, so a new visitor faces a
 * blank map with nothing saying where the data lives. Shown once: dismissed by its own close
 * button or by opening the manager, and never again after that (localStorage). Starts hidden
 * and flips visible in an effect so the server render and the first client paint agree.
 *
 * The storage key still says "rail" and deliberately so -- it is the same hint, and renaming it
 * would show it again to everyone who has already dismissed it.
 */
function ManagerHint() {
  const [isVisible, setIsVisible] = useState(false);

  useEffect(() => {
    setIsVisible(localStorage.getItem(MANAGER_HINT_DISMISSED_KEY) === null);
  }, []);

  // Opening the manager counts as having found it, written from a store SUBSCRIPTION rather
  // than from an effect on a rendered value: this component unmounts with the rail the instant
  // the manager opens, so there is no render in which it could observe `layerPanelOpen` true.
  useEffect(
    () =>
      usePanelStore.subscribe((state) => {
        if (state.layerPanelOpen) localStorage.setItem(MANAGER_HINT_DISMISSED_KEY, "1");
      }),
    []
  );

  if (!isVisible) return null;

  return (
    <div
      className="flex shrink-0 items-center gap-1.5 rounded-xl border border-(--glass-border) bg-(--glass-bg) py-1.5 pl-3 pr-1.5 text-xs text-[hsl(var(--foreground))] shadow-(--shadow-lg) [backdrop-filter:blur(var(--glass-blur))]"
      data-testid="manager-hint"
    >
      Turn on data layers here
      <button
        type="button"
        aria-label="Dismiss"
        onClick={() => {
          localStorage.setItem(MANAGER_HINT_DISMISSED_KEY, "1");
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
 * The whole map surface while the manager is collapsed: a way back in, and the legend.
 *
 * "Everything collapses away" is the interaction this component is the other half of. Collapsed,
 * the map carries exactly one row of chrome at its bottom-left corner, and that row's largest
 * part is the legend -- which is to say, the list of what is drawn. It unmounts entirely while
 * the manager is open, because both halves have a fuller form in there: the button becomes the
 * header's close control, and the legend becomes the layer tree. An unread-alert bell sat
 * between them until the alerts feature was removed on 2026-08-15.
 *
 * It sits where the bottom toolbar used to, not where the search field used to. The top-left
 * corner is deliberately left empty: that is where the manager's own header lands, and the
 * collision between the two was the defect this consolidation started from.
 */
export function ManagerRail() {
  const layerPanelOpen = usePanelStore((state) => state.layerPanelOpen);
  const toggleLayerPanel = usePanelStore((state) => state.toggleLayerPanel);
  const hasActiveLayers = useMapStore((state) => state.activeLayers.length > 0);

  if (layerPanelOpen) return null;

  return (
    <div
      data-testid="manager-rail"
      // `items-end` so the legend's chip row and the buttons share a baseline while the
      // taxonomy grows upward out of the row rather than displacing it.
      className="pointer-events-none absolute bottom-4 left-4 z-10 flex max-w-[calc(100vw-2rem)] items-end gap-2"
    >
      <div className="pointer-events-auto flex shrink-0 items-center gap-1 rounded-xl border border-(--glass-border) bg-(--glass-bg) p-1 shadow-(--shadow-lg) [backdrop-filter:blur(var(--glass-blur))]">
        <button
          type="button"
          title="Map manager"
          // The dot is the visual "the map is drawing something" signal; the name change is
          // its non-visual equivalent.
          aria-label={hasActiveLayers ? "Map manager (layers active)" : "Map manager"}
          // No `aria-controls`: this row unmounts the moment the manager opens, so the region
          // it would name is never in the document at the same time as this button.
          aria-expanded={false}
          onClick={toggleLayerPanel}
          // h-11 w-11 (44px) is the tap-target floor the rest of the map chrome holds to;
          // shrink-0 keeps it from squashing toward its 16px icon on a short viewport.
          className="relative flex h-11 w-11 shrink-0 items-center justify-center rounded-md text-[hsl(var(--foreground))] transition-colors hover:bg-[hsl(var(--accent))] hover:text-[hsl(var(--accent-foreground))] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[hsl(var(--ring))]"
        >
          <Layers className="h-4 w-4" />
          {hasActiveLayers && (
            <span className="absolute right-1 top-1 h-2.5 w-2.5 rounded-full bg-emerald-500 ring-2 ring-[hsl(var(--background))]" />
          )}
        </button>
      </div>

      <div className="pointer-events-auto flex min-w-0 items-end gap-2">
        <LayerLegend />
        <ManagerHint />
      </div>
    </div>
  );
}
