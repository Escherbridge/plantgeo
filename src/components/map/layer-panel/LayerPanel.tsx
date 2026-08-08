"use client";

import { useEffect } from "react";
import { Eye, EyeOff, PanelLeftClose } from "lucide-react";
import { DockSections } from "@/components/map/layer-panel/DockSections";
import {
  PANEL_FIXED_ROW,
  PANEL_SCROLLER,
  PANEL_SHELL,
} from "@/components/map/layer-panel/panel-scroll";
import { Button } from "@/components/ui/button";
import { useMap } from "@/lib/map/map-context";
import { useLayerStore } from "@/stores/layer-store";
import { usePanelStore } from "@/stores/panel-store";

/**
 * How far the camera's centre shifts when the dock opens, in CSS pixels: the panel's own
 * width. Kept next to `LAYER_PANEL_INSET` in MapView, which anchors the dock's own toggle and
 * the bottom-left toolbar to the same number. Unchanged by the 2026-08-08 merge: the dock
 * absorbed seven sheets without taking a pixel more from the map.
 */
const LAYER_PANEL_WIDTH_PX = 304;

/** The dock's own DOM id, so `DockToggle`'s `aria-controls` can name the region it opens. */
export const MAP_MANAGER_DOCK_ID = "map-manager-dock";

/**
 * The map's reaction to the dock, and what it deliberately is NOT.
 *
 * `padding` moves the camera's optical centre without touching canvas size, so nothing here
 * calls `resize()` and no tile is refetched -- which is exactly why the panel is an overlay
 * inside MapView rather than a MapLayout side panel that would reflow the canvas on every
 * collapse. It composes with `resetView` and `MapFocus`, both of which already move the
 * camera, because padding is camera state rather than a competing animation.
 *
 * `prefers-reduced-motion` gets `jumpTo`, as MapFocus already does.
 */
function useMapPaddingForPanel(isOpen: boolean): void {
  const map = useMap();

  useEffect(() => {
    if (!map) return;
    const left = isOpen ? LAYER_PANEL_WIDTH_PX : 0;
    // Optional-called: jsdom under vitest implements no `matchMedia`, and a test that hands
    // this component a fake map must not crash on a preference read.
    const prefersReducedMotion =
      typeof window !== "undefined" &&
      window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

    if (prefersReducedMotion === true) {
      map.jumpTo({ padding: { top: 0, bottom: 0, left, right: 0 } });
      return;
    }
    map.easeTo({ padding: { top: 0, bottom: 0, left, right: 0 }, duration: 250 });
  }, [map, isOpen]);
}

/**
 * The map management dock: the ONLY panel surface on the map since 2026-08-08.
 *
 * Every switchable layer in one place, grouped by category, each row carrying its eye, its
 * colour chip, its name and its opacity -- and under each category, the report that used to
 * open as a right-hand sheet. The seven sheets and the icon rail that opened them are gone;
 * see src/components/map/AGENTS.md "One dock, no sheets" for what that fixed.
 *
 * Deliberately not shipped here, and each for a reason rather than for time:
 * - **Drag reordering.** Paint order in this app is code, not data: it is the `beforeId` at
 *   each `addLayer` plus `style.load` listener registration order, which is load-bearing
 *   (ServiceAreaLayer's dimming mask must stay beneath the data pins). `activeLayers` is
 *   toggle-insertion order and means nothing spatially, and a `map.moveLayer` would be
 *   discarded by the next basemap swap, since every component re-adds its layers in mount
 *   order. A control that silently stops working at the style switcher is worse than none.
 * - **Blend modes.** MapLibre has no per-layer blend mode. Anything shipped under that label
 *   would be a fake.
 * - **Lock.** Photoshop's lock guards against direct manipulation on canvas. Nothing here
 *   moves or edits a layer, so it would guard against nothing.
 */
export function LayerPanel() {
  const isOpen = usePanelStore((state) => state.layerPanelOpen);
  const closeLayerPanel = usePanelStore((state) => state.closeLayerPanel);
  // The same boolean the corner legend card reads, so the two controls of one thing can never
  // contradict each other.
  const legendVisible = useLayerStore((state) => state.legendVisible);
  const toggleLegend = useLayerStore((state) => state.toggleLegend);

  useMapPaddingForPanel(isOpen);

  if (!isOpen) return null;

  return (
    // "Map manager" rather than "Map layers": since the merge this landmark contains the
    // layer list AND all eight reports, and a landmark that under-names itself sends a screen
    // reader user looking elsewhere for what is already inside it.
    <aside
      id={MAP_MANAGER_DOCK_ID}
      aria-label="Map manager"
      data-testid="layer-panel"
      className={PANEL_SHELL}
    >
      <header
        className={`${PANEL_FIXED_ROW} flex items-center justify-between gap-2 border-b border-(--glass-border) px-3 py-2`}
      >
        <h2 className="text-xs font-semibold uppercase tracking-wide text-[hsl(var(--muted-foreground))]">
          Map Manager
        </h2>
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="icon"
            className="h-8 w-8 max-sm:h-11 max-sm:w-11"
            aria-label={legendVisible ? "Hide legend entries" : "Show legend entries"}
            aria-pressed={legendVisible}
            onClick={toggleLegend}
          >
            {legendVisible ? (
              <Eye className="h-3.5 w-3.5" />
            ) : (
              <EyeOff className="h-3.5 w-3.5" />
            )}
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className="h-8 w-8 max-sm:h-11 max-sm:w-11"
            aria-label="Close map manager"
            onClick={closeLayerPanel}
          >
            <PanelLeftClose className="h-3.5 w-3.5" />
          </Button>
        </div>
      </header>

      {/* The one scrolling element in this panel -- see panel-scroll.ts for why that is a
          contract and not a preference. It matters more now than it did with a tree in it:
          every former sheet's body scrolls in HERE, and each one arrived carrying its own
          `overflow-y-auto max-h-[calc(100vh-8rem)]` wrapper, which is the second-scrollbar
          defect rule 2 was written against. Those wrappers were stripped on the way in. */}
      <div className={PANEL_SCROLLER}>
        <DockSections />
      </div>

      <footer
        className={`${PANEL_FIXED_ROW} border-t border-(--glass-border) px-3 py-2 text-[10px] leading-relaxed text-[hsl(var(--muted-foreground))]`}
      >
        The eye switches a layer off; the slider only changes how strongly it draws. Open a
        category&rsquo;s report for what it measures.
      </footer>
    </aside>
  );
}
