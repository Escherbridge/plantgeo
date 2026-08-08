import { create } from "zustand";
import { devtools } from "zustand/middleware";
import {
  layerRegistryEntries,
  panelIdForLayerToggle,
  type LayerToggleId,
} from "@/lib/map/layer-registry";
import { useMapStore } from "@/stores/map-store";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/**
 * A category of map data. Named `PanelId` for the seven right-hand sheets it once addressed;
 * since 2026-08-08 there are no sheets and it names a SECTION of the left dock instead. The
 * registry still routes every layer to one of these, so the vocabulary outlived its surface.
 */
export type PanelId =
  | "fire"
  | "water"
  | "vegetation"
  | "soil"
  | "climate"
  | "community"
  | "team"
  | "analytics";

/**
 * A dock section that can open a details region.
 *
 * The seven categories plus "alerts", which owns no layer and no registry entry: it is the
 * warnings surface the toolbar bell used to open in its own sheet.
 *
 * Every member has a REPORT behind it -- `DETAILS_LABELS` and `DETAILS_BODIES` are both
 * exhaustive over this union, so a new member is a compile error in two files until it has a
 * title and a body. That is why "time" is not in here: see `DockSectionId`.
 */
export type DockDetailsId = PanelId | "alerts";

/**
 * Anything the dock can expand and scroll to, which is a wider set than the reports.
 *
 * "time" joined on 2026-08-08, when the scrubber moved out of the top-right region and into
 * the top of the dock's scroller. It is a section by every behaviour that matters here -- it
 * expands, it collapses, and `focusDockSection("time")` is what the top-bar date pill calls --
 * but it is not a `DockDetailsId`, because it has no dynamically-imported report and no
 * warehouse queries of its own. Widening the id vocabulary rather than the report vocabulary
 * is what keeps `DockDetailsBody`'s exhaustive record honest about what a report is.
 */
export type DockSectionId = DockDetailsId | "time";

/**
 * The sections a cold load opens, and the only ones that may be seeded.
 *
 * "time" alone, and the reason is the same economics that keep every report shut: expanding a
 * report MOUNTS it and fires its warehouse queries, while the Time section's body is the
 * scrubber card, whose capabilities arrive from the always-mounted
 * `TimeSliderCapabilitiesLoader` whether the dock is open or not. It therefore costs nothing to
 * have open, and the map date is what a reader most often came to the dock to change.
 *
 * `readonly`, because this is a declaration and not a working list: the store seeds a COPY of it
 * (`[...INITIALLY_EXPANDED_SECTIONS]`), and a mutable export would let any importer push a
 * section into the cold-load set from the far side of the module -- silently mounting a report
 * and firing its warehouse queries before anyone asked.
 */
export const INITIALLY_EXPANDED_SECTIONS: readonly DockSectionId[] = ["time"] as const;

/**
 * Maps each category to the layer IDs it governs, inverted from the layer registry so a
 * layer's owning section is declared once, next to the rest of that layer's wiring.
 * Categories with no layers of their own still get an entry, hence the seeded record.
 */
function buildPanelLayerMap(): Record<PanelId, LayerToggleId[]> {
  const panelLayers: Record<PanelId, LayerToggleId[]> = {
    fire:       [],
    water:      [],
    vegetation: [],
    soil:       [],
    climate:    [],
    community:  [],
    analytics:  [],
    team:       [],
  };
  for (const entry of layerRegistryEntries()) {
    if (entry.panelId !== null) panelLayers[entry.panelId].push(entry.toggleId);
  }
  return panelLayers;
}

const PANEL_LAYER_MAP: Record<PanelId, LayerToggleId[]> = buildPanelLayerMap();

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------

interface PanelState {
  /**
   * Whether the left-edge dock is open. The dock is the ONLY panel surface: the seven
   * right-hand sheets and the icon rail that opened them were removed on 2026-08-08 -- see
   * src/components/map/AGENTS.md "One dock, no sheets".
   *
   * Starts closed: the map opens with every layer off, and a 19rem dock over the canvas is
   * not what a first-time visitor is owed before they have asked for anything.
   *
   * It is NOT visibility state -- `map-store.activeLayers` remains the single source of that.
   */
  layerPanelOpen: boolean;

  /**
   * Dock sections currently expanded.
   *
   * Load-bearing rather than cosmetic: a details region is where every panel query lives, and
   * it is MOUNTED only while its id is in here. Opening the dock therefore costs nothing, the
   * way opening the old rail cost nothing -- the alternative, mounting all eight, would fire
   * every panel's queries at once on the first click. Several may be open at a time; that is
   * the reader's call, not a limit worth enforcing.
   *
   * Seeded with `INITIALLY_EXPANDED_SECTIONS`, which is "time" and nothing else -- the one
   * section whose body owns no query, so the rule above has nothing to say about it.
   *
   * Deliberately not persisted: a section left open a week ago would re-issue its warehouse
   * queries on the next cold load, before anyone had asked for them.
   */
  expandedDetails: DockSectionId[];

  /**
   * A section asked to be brought into view, consumed by the section that scrolls itself.
   *
   * The dock's body is the one scroller (see panel-scroll.ts), so "open the dock at Alerts"
   * is two facts -- expand it, and put it on screen -- and the second cannot be expressed as
   * state the section merely renders from. Cleared by the section on arrival so a later
   * expansion by hand does not re-scroll.
   */
  pendingScrollSection: DockSectionId | null;

  // --- Actions ---
  /** Dock or undock the panel. */
  toggleLayerPanel: () => void;
  /** Undock without touching any layer. */
  closeLayerPanel: () => void;
  /** Expand or collapse one section. */
  toggleDetails: (id: DockSectionId) => void;
  /**
   * Open the dock at a section: dock it, expand that section, and ask it to scroll into view.
   * The one call a shortcut outside the dock makes -- the toolbar's alert bell and, since
   * 2026-08-08, the top-bar date pill, which is the whole of what that pill's click does.
   */
  focusDockSection: (id: DockSectionId) => void;
  /** Marks the scroll request served; called by the section that scrolled. */
  clearPendingScroll: () => void;
}

export const usePanelStore = create<PanelState>()(
  devtools(
    (set) => ({
      layerPanelOpen: false,
      expandedDetails: [...INITIALLY_EXPANDED_SECTIONS],
      pendingScrollSection: null,

      toggleLayerPanel: () => set((s) => ({ layerPanelOpen: !s.layerPanelOpen })),

      closeLayerPanel: () => set({ layerPanelOpen: false }),

      toggleDetails: (id) =>
        set((s) => ({
          expandedDetails: s.expandedDetails.includes(id)
            ? s.expandedDetails.filter((open) => open !== id)
            : [...s.expandedDetails, id],
        })),

      focusDockSection: (id) =>
        set((s) => ({
          layerPanelOpen: true,
          expandedDetails: s.expandedDetails.includes(id)
            ? s.expandedDetails
            : [...s.expandedDetails, id],
          pendingScrollSection: id,
        })),

      clearPendingScroll: () => set({ pendingScrollSection: null }),
    }),
    { name: "panel-store" },
  ),
);

// ---------------------------------------------------------------------------
// Selectors (pure functions, no hooks — safe for use anywhere)
// ---------------------------------------------------------------------------

/** Get the layer IDs associated with a category. */
export function getLayersForPanel(panelId: PanelId): string[] {
  return PANEL_LAYER_MAP[panelId];
}

/** Get the category that owns a layer ID (if any). */
export function getPanelForLayer(layerId: string): PanelId | null {
  return panelIdForLayerToggle(layerId);
}

/** All layer IDs managed by any category. */
export function getAllManagedLayerIds(): string[] {
  return Object.values(PANEL_LAYER_MAP).flat();
}

// ---------------------------------------------------------------------------
// Derived hooks (compose panel-store + map-store)
// ---------------------------------------------------------------------------

/**
 * Hook: does the given category have any active (visible) layers?
 *
 * The rail's per-button emerald badge was its only consumer, and the rail is gone: the dock
 * shows the same fact more precisely, as each group's `n of m` count and tri-state group eye.
 * Kept because `PANEL_LAYER_MAP`'s aggregation is what it exercises, and
 * `src/__tests__/stores/panel-store.test.ts` uses it to guard the inversion from the registry.
 */
export function usePanelHasActiveLayers(panelId: PanelId): boolean {
  const activeLayers = useMapStore((s) => s.activeLayers);
  return PANEL_LAYER_MAP[panelId].some((id) => activeLayers.includes(id));
}

/** Hook: is one section expanded -- and, for a report section, therefore mounted? */
export function useDetailsExpanded(id: DockSectionId): boolean {
  return usePanelStore((s) => s.expandedDetails.includes(id));
}
