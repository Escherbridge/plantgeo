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
 * new category fails to compile here rather than rendering an unnamed group.
 */
export const GROUP_LABELS: Record<PanelId, string> = {
  fire: "Fire",
  water: "Water",
  vegetation: "Vegetation",
  soil: "Soil",
  climate: "Climate",
  community: "Community",
  team: "Teams",
  analytics: "Analytics",
};

/**
 * Where a layer that no category governs is filed. Only `building-footprints` lands here: no
 * category's report describes it, and this bucket is what keeps the one comprehensive list of
 * layers complete. Its row is also its ONLY switch since 2026-08-09 -- the bottom toolbar's
 * building button was a second control over the same `activeLayers` entry, and went with the
 * toolbar.
 */
export const UNGOVERNED_GROUP_KEY = "Basemap";

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
  analytics: "Environmental Analytics",
  alerts: "Environmental Alerts",
};

/**
 * Every section the manager can render: the categories, the ungoverned bucket, the alerts
 * pivot, and the three query-free sections at the top of the scroller.
 *
 * Wider than `DockDetailsId` because a section is not the same thing as a report -- "Basemap"
 * carries layers and no report; "search", "time" and "view" carry a control and no report.
 * `DockSectionId` brings those three in; the ungoverned bucket is a local sentinel and joins
 * from here.
 */
export type DockSectionKey = DockSectionId | typeof UNGOVERNED_GROUP_KEY;

/** One category of layers, with the details region filed under it. */
export interface DockLayerGroup {
  /** Stable key: the category id, or the sentinel for layers no category governs. */
  key: PanelId | typeof UNGOVERNED_GROUP_KEY;
  label: string;
  layerIds: LayerToggleId[];
  /** The details region this group discloses, or null for a group that has no report. */
  detailsId: DockDetailsId | null;
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
    const key = entry.panelId ?? UNGOVERNED_GROUP_KEY;
    let group = byKey.get(key);
    if (group === undefined) {
      group = {
        key,
        label: entry.panelId === null ? UNGOVERNED_GROUP_KEY : GROUP_LABELS[entry.panelId],
        layerIds: [],
        detailsId: entry.panelId,
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
 * Sections that own no layer, so the registry cannot order them: two categories whose reports
 * describe the whole map rather than one feed, and the warnings surface the toolbar bell used
 * to open in its own sheet. They sit below the layer groups, where a reader who has finished
 * with the layer list finds them.
 */
export const DOCK_PIVOT_SECTIONS: DockDetailsId[] = ["alerts", "team", "analytics"];

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
 * render the same id twice for all seven categories. Nothing reads either id today -- scroll
 * addresses a section by ref -- so this exists only to keep the DOM valid.
 */
export function dockGroupDomId(key: DockLayerGroup["key"]): string {
  return `dock-group-${key}`;
}
