/**
 * The map manager's structure, derived once and free of React.
 *
 * Free of React on purpose: `src/__tests__/lib/map/layer-registry.test.ts` asserts that every
 * registry layer is reachable from the dock, and it must be able to ask that question without
 * mounting the map, tRPC and every store the sections read. Before 2026-08-08 the same test
 * answered it by regex-scanning `<LayerToggle>` out of the panel sources, because a sheet's
 * JSX was unreachable from anywhere; the dock derives its rows from the registry instead, so
 * the question is now answerable by import.
 *
 * Rationale for the shape -- one dock, no sheets -- is in src/components/map/AGENTS.md.
 */

import { layerRegistryEntries, type LayerToggleId } from "@/lib/map/layer-registry";
import type { DockDetailsId, DockSectionId, PanelId } from "@/stores/panel-store";

/**
 * The name each group of layers carries in the dock.
 *
 * Deliberately the CATEGORY, not the details region's title: "Fire" names the set of layers,
 * "Fire Dashboard" names the report under them. The record is exhaustive over `PanelId`, so a
 * new category fails to compile here rather than rendering an unnamed group -- which is why
 * "Teams" is in here despite owning no layer and so never heading a group.
 */
export const GROUP_LABELS: Record<PanelId, string> = {
  fire: "Fire",
  water: "Water",
  vegetation: "Vegetation",
  soil: "Soil",
  climate: "Climate",
  community: "Community",
  team: "Teams",
};

/**
 * The title a details region carries.
 *
 * Byte-identical to the `SheetTitle` each of these regions rendered as a right-hand sheet,
 * so the merge renamed nothing a reader already knew. `community` is the one exception: its
 * sheet title was computed from the active organization and still is, inside the region --
 * a disclosure that changes its own name when a workspace is selected is not a label.
 */
export const DETAILS_LABELS: Record<DockDetailsId, string> = {
  fire: "Fire Dashboard",
  water: "Water Scarcity",
  vegetation: "Vegetation & Land Cover",
  soil: "Soil Health & Carbon",
  // The one region with no sheet predecessor: the Climate section was added on 2026-08-08
  // with the NASA POWER field, after the sheets were gone.
  climate: "Climate & Weather History",
  community: "Strategy Requests",
  team: "Team Dashboard",
  // The other region with no sheet predecessor: it was a floating bottom-right toggle + panel
  // in `MapView` until 2026-08-14, when it joined the dock as the reviewer-mandated fix for
  // the second control surface the map had grown outside "One manager, no floating surfaces".
  offline: "Offline & Sync",
};

/**
 * Every section the manager can render: the categories, plus the two query-free sections at
 * the top of the scroller.
 *
 * Wider than `DockDetailsId` because a section is not the same thing as a report -- "search"
 * and "view" carry a control and no report. "time" was a third such section for one day: the
 * map-wide scrubber it held is gone, and each layer's own slider lives on its own `LayerRow`
 * instead. A "Basemap" bucket sat here too, holding the layers no category governed; it went
 * with `building-footprints`, the only layer that was ever in it.
 */
export type DockSectionKey = DockSectionId;

/** One category of layers, with the details region filed under it. */
export interface DockLayerGroup {
  /** Stable key: the category id, which is also the id of the report filed under it. */
  key: PanelId;
  label: string;
  layerIds: LayerToggleId[];
  detailsId: DockDetailsId;
}

/**
 * Groups in registry declaration order, layers in registry declaration order within them.
 *
 * Derived, not listed: a new registry entry appears in the dock with no edit here, and the
 * ordering guarantee is the same one `activeLegendEntries` gives the legend, so neither
 * surface reshuffles as toggles come and go.
 */
function buildLayerGroups(): DockLayerGroup[] {
  const groups: DockLayerGroup[] = [];
  const byKey = new Map<string, DockLayerGroup>();

  for (const entry of layerRegistryEntries()) {
    const key = entry.panelId;
    let group = byKey.get(key);
    if (group === undefined) {
      group = {
        key,
        label: GROUP_LABELS[key],
        layerIds: [],
        detailsId: key,
      };
      byKey.set(key, group);
      groups.push(group);
    }
    group.layerIds.push(entry.toggleId);
  }

  return groups;
}

export const DOCK_LAYER_GROUPS: DockLayerGroup[] = buildLayerGroups();

/**
 * Sections that own no layer, so the registry cannot order them: the category whose report
 * describes the whole workspace rather than one feed, and the offline/sync surface the
 * bottom-right floating toggle used to open before it joined the dock. They sit below the
 * layer groups, where a reader who has finished with the layer list finds them.
 */
export const DOCK_PIVOT_SECTIONS: DockDetailsId[] = ["team", "offline"];

/**
 * Every layer the dock renders a row for.
 *
 * The claim `layer-registry.test.ts` checks against `unreachableLayerToggleIds()`: a layer
 * with no row here has no switch anywhere, which is exactly the bug (`sensors` and
 * `evacuation-zones`, published and toggleable by nothing) that check was written for.
 */
export function dockReachableLayerToggleIds(): LayerToggleId[] {
  return DOCK_LAYER_GROUPS.flatMap((group) => group.layerIds);
}

/** The DOM id of a dock section, so a scroll request can find it. */
export function dockSectionDomId(key: DockSectionKey): string {
  return `dock-section-${key}`;
}

/**
 * The DOM id of a layer GROUP's `<section>`, distinct from `dockSectionDomId`.
 *
 * A group's key and its details region's id are the same string for every category (both are
 * the `PanelId`), so the group `<section>` and the `DetailsSection` it wraps would otherwise
 * render the same id twice for every one of them. Nothing reads either id today -- scroll
 * addresses a section by ref -- so this exists only to keep the DOM valid.
 */
export function dockGroupDomId(key: DockLayerGroup["key"]): string {
  return `dock-group-${key}`;
}
