"use client";

import { useEffect, useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  MapPinned,
  PanelLeftClose,
  type LucideIcon,
} from "lucide-react";
import { DockSections } from "@/components/map/layer-panel/DockSections";
import {
  PANEL_FIXED_ROW,
  PANEL_SCROLLER,
  PANEL_SHELL,
} from "@/components/map/layer-panel/panel-scroll";
import { SearchDockSection } from "@/components/map/layer-panel/SearchDockSection";
import { ViewDockSection } from "@/components/map/layer-panel/ViewDockSection";
import { Button } from "@/components/ui/button";
import { useMap } from "@/lib/map/map-context";
import { usePanelStore } from "@/stores/panel-store";
import {
  LAND_CONTEXT_GROUP_IDS,
  LAND_CONTEXT_GROUP_LABELS,
  useLandContextStore,
} from "@/stores/land-context-store";

/**
 * How far the camera's centre shifts when the manager opens, in CSS pixels: the panel's own
 * width. Unchanged by either merge -- the manager absorbed seven sheets, the search field and
 * the bottom toolbar without taking a pixel more from the map.
 */
const LAYER_PANEL_WIDTH_PX = 304;

/** The manager's own DOM id, so a shortcut's `aria-controls` can name the region it opens. */
export const MAP_MANAGER_DOCK_ID = "map-manager-dock";

/**
 * The map's reaction to the manager, and what it deliberately is NOT.
 *
 * `padding` moves the camera's optical centre without touching canvas size, so nothing here
 * calls `resize()` and no tile is refetched -- which is exactly why the panel is an overlay
 * inside MapView rather than a MapLayout side panel that would reflow the canvas on every
 * collapse. It composes with `resetView` and `MapFocus`, both of which already move the
 * camera, because padding is camera state rather than a competing animation.
 *
 * A phone gets no padding at all: there the manager is a full-screen overlay, so there is no
 * remaining map to re-centre, and shifting the camera under it would leave the reader looking
 * somewhere else when they dismissed it.
 *
 * `prefers-reduced-motion` gets `jumpTo`, as MapFocus already does.
 */
function useMapPaddingForPanel(isOpen: boolean): void {
  const map = useMap();

  useEffect(() => {
    if (!map) return;
    // Optional-called: jsdom under vitest implements no `matchMedia`, and a test that hands
    // this component a fake map must not crash on a preference read.
    const isPhoneLayout = window.matchMedia?.("(max-width: 639px)").matches === true;
    const left = isOpen && !isPhoneLayout ? LAYER_PANEL_WIDTH_PX : 0;
    const prefersReducedMotion =
      typeof window !== "undefined" &&
      window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

    const applyPadding = () => {
      if (prefersReducedMotion === true) {
        map.jumpTo({ padding: { top: 0, bottom: 0, left, right: 0 } });
        return;
      }
      map.easeTo({ padding: { top: 0, bottom: 0, left, right: 0 }, duration: 250 });
    };
    if (map.isMoving?.()) {
      map.once("moveend", applyPadding);
      return () => { map.off("moveend", applyPadding); };
    }
    applyPadding();
  }, [map, isOpen]);
}

/**
 * One land-context group: an icon, its label, and an animated switch. Styled identically to
 * `ViewDockSection`'s `RenderModeRow` -- the closest existing precedent for a control that
 * toggles something other than a `LayerToggleId` in `activeLayers`. Land-context groups paint
 * through `LandContextLayer` off `land-context-store.enabledGroups`, not through the registry,
 * so they cannot join `DockSections`' `LayerGroupSection` list without becoming a real
 * `LayerToggleId` -- out of scope for this file alone.
 */
function LandContextGroupRow({
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
 * "Land context": the four independently toggleable land-context groups (parcels/land use,
 * electric utility territories, BLM lands, state-managed lands -- `LAND_CONTEXT_GROUP_IDS`).
 *
 * Shares `ControlDockSection`'s caret-and-label shell by hand rather than by import: that
 * component types its `id` as `DockSectionId`, a closed union owned by `panel-store.ts`, and
 * this file's edit scope is `LayerPanel.tsx` alone. Local `useState` in place of
 * `panel-store`'s `expandedDetails` is the one deliberate deviation from the shared shell --
 * this section issues no query when opened (`land-context-store` is pure client toggle state),
 * so there is nothing here for a shared "close on navigate" or "scroll to on shortcut" behaviour
 * to coordinate with the way there is for a report section.
 */
function LandContextDockSection() {
  const [isExpanded, setIsExpanded] = useState(true);
  const enabledGroups = useLandContextStore((state) => state.enabledGroups);
  const toggleGroup = useLandContextStore((state) => state.toggleGroup);

  return (
    <div
      data-testid="dock-section-land-context"
      className="border-b border-(--glass-border) pb-1"
    >
      <button
        type="button"
        aria-expanded={isExpanded}
        aria-controls="land-context-dock-section-body"
        onClick={() => setIsExpanded((expanded) => !expanded)}
        className={[
          "flex min-h-8 w-full items-center gap-1.5 rounded-md px-1 py-1 text-left text-[11px]",
          "font-medium transition-colors focus-visible:outline-none focus-visible:ring-2",
          "focus-visible:ring-[hsl(var(--ring))] max-sm:min-h-11",
          isExpanded
            ? "bg-[hsl(var(--muted)/0.5)] text-[hsl(var(--foreground))]"
            : "text-[hsl(var(--muted-foreground))] hover:bg-[hsl(var(--muted)/0.4)] hover:text-[hsl(var(--foreground))]",
        ].join(" ")}
      >
        {isExpanded ? (
          <ChevronDown aria-hidden="true" className="h-3.5 w-3.5 shrink-0" />
        ) : (
          <ChevronRight aria-hidden="true" className="h-3.5 w-3.5 shrink-0" />
        )}
        <MapPinned aria-hidden="true" className="h-3.5 w-3.5 shrink-0 opacity-70" />
        <span className="min-w-0 flex-1 truncate">Land context</span>
      </button>

      {isExpanded && (
        <div
          id="land-context-dock-section-body"
          data-testid="dock-section-body-land-context"
          className="mb-1 mt-1 flex flex-col gap-1"
        >
          {LAND_CONTEXT_GROUP_IDS.map((group) => (
            <LandContextGroupRow
              key={group}
              icon={MapPinned}
              label={LAND_CONTEXT_GROUP_LABELS[group]}
              isOn={enabledGroups[group]}
              onToggle={() => toggleGroup(group)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * The map manager: the ONLY control surface on the map since 2026-08-09.
 *
 * Everything a reader can change about this map is a section of this one column. Search, the
 * map date, render mode, then every switchable layer grouped by category with the report filed
 * under it. The seven right-hand sheets and their icon rail went on 2026-08-08; the floating
 * search field, the bottom toolbar and the corner legend card went on 2026-08-09. See
 * src/components/map/AGENTS.md "One manager, no floating surfaces" for what each removal fixed.
 *
 * What is left outside it collapses with it, into one row at the bottom-left corner
 * (`ManagerRail`): the button back in, the unread alert count, and the legend -- which is the
 * chips for what is drawn, not a second reading of layer state. The date pill above the canvas
 * is the one other thing, and it is a marker rather than a control.
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

  useMapPaddingForPanel(isOpen);

  if (!isOpen) return null;

  return (
    // "Map manager" rather than "Map layers": this landmark contains the layer list, all eight
    // reports, search, the date and render mode, and a landmark that under-names itself sends a
    // screen reader user looking elsewhere for what is already inside it.
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
        {/* One button, because there is one thing to do here: put it away. The legend eye that
            used to sit beside it governed a corner card that no longer exists -- the legend is
            the collapsed state of this panel now, so hiding it from inside it was a control
            over something the reader could not see while using it. */}
        <Button
          variant="ghost"
          size="icon"
          className="h-8 w-8 max-sm:h-11 max-sm:w-11"
          aria-label="Close map manager"
          onClick={closeLayerPanel}
        >
          <PanelLeftClose className="h-3.5 w-3.5" />
        </Button>
      </header>

      {/* The one scrolling element in this panel -- see panel-scroll.ts for why that is a
          contract and not a preference, and for why its scrollbar is unpainted while every
          scroll gesture and key still reaches it. */}
      <div className={PANEL_SCROLLER}>
        {/* The two control sections lead, in the order a reader needs them: where, and how it
            is drawn. Both govern the whole map rather than one category, so a control filed
            among the categories would read as belonging to whichever one it landed beside.
            They scroll with the rest -- pinning them would be fixed rows competing with the
            header for a 19rem column's height.

            WHEN is no longer among them, and deliberately: since 2026-08-09 each layer carries
            its own day on its own row, so a section here would be a control over a map-wide
            date that no longer exists. */}
        <SearchDockSection />
        <ViewDockSection />
        <LandContextDockSection />
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
