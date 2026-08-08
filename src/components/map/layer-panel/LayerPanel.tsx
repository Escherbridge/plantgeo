"use client";

import { useEffect } from "react";
import { Eye, EyeOff, PanelLeftClose } from "lucide-react";
import { LayerTree } from "@/components/map/layer-panel/LayerTree";
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
 * width. Kept next to `LAYER_PANEL_INSET` in MapView, which anchors the rail and the toolbar
 * to the same number.
 */
const LAYER_PANEL_WIDTH_PX = 304;

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
    const prefersReducedMotion =
      typeof window !== "undefined" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    if (prefersReducedMotion) {
      map.jumpTo({ padding: { top: 0, bottom: 0, left, right: 0 } });
      return;
    }
    map.easeTo({ padding: { top: 0, bottom: 0, left, right: 0 }, duration: 250 });
  }, [map, isOpen]);
}

/**
 * The layer management dock: every switchable layer in one place, grouped by category, each
 * row carrying its eye, its colour chip, its name and its opacity.
 *
 * It is an ADDITIONAL surface, not a replacement: the right-hand sheets keep their switches,
 * and both write `map-store.activeLayers`, which src/components/map/AGENTS.md makes the single
 * source of layer visibility -- so the two cannot disagree by construction.
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
    <aside aria-label="Map layers" data-testid="layer-panel" className={PANEL_SHELL}>
      <header
        className={`${PANEL_FIXED_ROW} flex items-center justify-between gap-2 border-b border-(--glass-border) px-3 py-2`}
      >
        <h2 className="text-xs font-semibold uppercase tracking-wide text-[hsl(var(--muted-foreground))]">
          Layers
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
            aria-label="Close layer panel"
            onClick={closeLayerPanel}
          >
            <PanelLeftClose className="h-3.5 w-3.5" />
          </Button>
        </div>
      </header>

      {/* The one scrolling element in this panel -- see panel-scroll.ts for why that is a
          contract and not a preference. */}
      <div className={PANEL_SCROLLER}>
        <LayerTree />
      </div>

      <footer
        className={`${PANEL_FIXED_ROW} border-t border-(--glass-border) px-3 py-2 text-[10px] leading-relaxed text-[hsl(var(--muted-foreground))]`}
      >
        The eye switches a layer off. The slider only changes how strongly it draws.
      </footer>
    </aside>
  );
}
