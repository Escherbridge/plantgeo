"use client";

import { useCallback, useMemo, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { LayerRow } from "@/components/map/layer-panel/LayerRow";
import {
  DEFAULT_LEGEND_CONTEXT,
  type LegendContext,
} from "@/lib/map/layer-legends";
import {
  LAYER_REGISTRY,
  layerRegistryEntries,
  type LayerToggleId,
} from "@/lib/map/layer-registry";
import {
  useLayerVisibility,
  useSoilDisplayMode,
  useToggleLayer,
} from "@/lib/map/layer-toggle-context";
import type { PanelId } from "@/stores/panel-store";
import { useVegetationStore } from "@/stores/vegetation-store";

/**
 * The name each group of layers carries in the tree.
 *
 * Deliberately the CATEGORY, not the rail button's label: the rail opens "Fire Dashboard",
 * a report, while this heading names the set of layers under it. The record is exhaustive
 * over `PanelId`, so a new panel fails to compile here rather than rendering an unnamed group.
 */
const GROUP_LABELS: Record<PanelId, string> = {
  fire: "Fire",
  water: "Water",
  vegetation: "Vegetation",
  soil: "Soil",
  community: "Community",
  team: "Teams",
  analytics: "Analytics",
};

/**
 * Where a layer that no panel governs is filed. Only `building-footprints` lands here: it is
 * switched from the MapControls toolbar rather than from any sheet, which is precisely why the
 * tree has to carry it -- otherwise the one comprehensive list of layers would be missing one.
 */
const UNGOVERNED_GROUP_LABEL = "Basemap";

interface LayerGroup {
  /** Stable key: the panel id, or the sentinel for layers no panel governs. */
  key: string;
  label: string;
  layerIds: LayerToggleId[];
}

/**
 * Groups in registry declaration order, layers in registry declaration order within them.
 *
 * Derived, not listed: a new registry entry appears in the tree with no edit here, and the
 * ordering guarantee is the same one `activeLegendEntries` gives the legend, so neither
 * surface reshuffles as toggles come and go.
 */
function buildLayerGroups(): LayerGroup[] {
  const groups: LayerGroup[] = [];
  const byKey = new Map<string, LayerGroup>();

  for (const entry of layerRegistryEntries()) {
    const key = entry.panelId ?? UNGOVERNED_GROUP_LABEL;
    let group = byKey.get(key);
    if (group === undefined) {
      group = {
        key,
        label: entry.panelId === null ? UNGOVERNED_GROUP_LABEL : GROUP_LABELS[entry.panelId],
        layerIds: [],
      };
      byKey.set(key, group);
      groups.push(group);
    }
    group.layerIds.push(entry.toggleId);
  }

  return groups;
}

const LAYER_GROUPS = buildLayerGroups();

/**
 * One category: a disclosure caret, a tri-state group eye, and an `n of m` count.
 *
 * The group eye is `aria-checked="mixed"` when some but not all of its layers are on -- the
 * only honest reading of a control that governs several switches. Clicking it turns the group
 * off when anything in it is on, and on otherwise, which makes "clear this category" one
 * click; that is the behaviour a partial state most often precedes.
 */
function LayerGroupSection({
  group,
  legendContext,
}: {
  group: LayerGroup;
  legendContext: LegendContext;
}) {
  const [isExpanded, setIsExpanded] = useState(true);
  const layerVisibility = useLayerVisibility();
  const toggleLayer = useToggleLayer();

  // Withheld layers read false in useLayerVisibility whatever activeLayers says, so they are
  // excluded from the count rather than counted as permanently off -- "0 of 1" for a layer
  // nobody can switch on would report a gap that is really a governance decision.
  const controllable = useMemo(
    () =>
      group.layerIds.filter(
        (layerId) => LAYER_REGISTRY[layerId].permanentlyUnavailableReason === null
      ),
    [group.layerIds]
  );
  const activeCount = controllable.filter((layerId) => layerVisibility[layerId]).length;
  const allActive = controllable.length > 0 && activeCount === controllable.length;
  const someActive = activeCount > 0;

  const handleGroupToggle = useCallback(() => {
    // Only the layers that actually need to move are toggled: `toggleLayer` flips, so
    // toggling the whole group unconditionally would invert it rather than set it.
    for (const layerId of controllable) {
      if (layerVisibility[layerId] === someActive) toggleLayer(layerId);
    }
  }, [controllable, layerVisibility, someActive, toggleLayer]);

  const listId = `layer-group-${group.key}`;

  return (
    <section data-testid={`layer-group-${group.key}`}>
      <div className="flex items-center gap-1 px-1 py-1">
        <button
          type="button"
          onClick={() => setIsExpanded((expanded) => !expanded)}
          aria-expanded={isExpanded}
          aria-controls={listId}
          className="flex min-h-8 flex-1 items-center gap-1 rounded text-left text-[11px] font-semibold uppercase tracking-wide text-[hsl(var(--muted-foreground))] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--ring))] max-sm:min-h-11"
        >
          {isExpanded ? (
            <ChevronDown aria-hidden="true" className="h-3.5 w-3.5" />
          ) : (
            <ChevronRight aria-hidden="true" className="h-3.5 w-3.5" />
          )}
          {group.label}
          <span className="ml-1 font-normal normal-case tracking-normal opacity-70">
            {activeCount} of {controllable.length}
          </span>
        </button>
        <button
          type="button"
          role="switch"
          aria-checked={allActive ? true : someActive ? "mixed" : false}
          aria-label={`Show all ${group.label} layers on map`}
          disabled={controllable.length === 0}
          onClick={handleGroupToggle}
          className="flex h-8 w-8 items-center justify-center rounded text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--foreground))] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--ring))] disabled:cursor-not-allowed disabled:opacity-40 max-sm:h-11 max-sm:w-11"
        >
          <span
            aria-hidden="true"
            className={[
              "block h-3 w-3 rounded-[3px] border",
              allActive
                ? "border-emerald-500 bg-emerald-500"
                : someActive
                  ? "border-emerald-500 bg-emerald-500/40"
                  : "border-[hsl(var(--border))]",
            ].join(" ")}
          />
        </button>
      </div>
      {isExpanded && (
        <ul id={listId} className="flex flex-col">
          {group.layerIds.map((layerId) => (
            <LayerRow key={layerId} layerId={layerId} legendContext={legendContext} />
          ))}
        </ul>
      )}
    </section>
  );
}

/**
 * The whole layer set, grouped by category.
 *
 * The display modes that change what a layer PAINTS are read here once and handed down, so
 * seventeen rows do not each open their own store subscriptions for the same two fields. Read
 * straight from the vegetation store rather than through `useVegetationDisplayMode`, which
 * also projects the slider's settled day -- a per-mount settle timer that moves nothing on a
 * colour chip.
 */
export function LayerTree() {
  const soilDisplayMode = useSoilDisplayMode();
  const vegetationMode = useVegetationStore((state) => state.mode);
  const ndviMode = useVegetationStore((state) => state.ndviMode);

  const legendContext = useMemo<LegendContext>(
    () => ({
      ...DEFAULT_LEGEND_CONTEXT,
      vegetationMode,
      ndviMode,
      soilFieldDepth: soilDisplayMode.fieldDepth,
    }),
    [vegetationMode, ndviMode, soilDisplayMode.fieldDepth]
  );

  return (
    <div className="flex flex-col gap-1">
      {LAYER_GROUPS.map((group) => (
        <LayerGroupSection key={group.key} group={group} legendContext={legendContext} />
      ))}
    </div>
  );
}
