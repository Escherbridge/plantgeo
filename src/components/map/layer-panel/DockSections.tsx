"use client";

import { useCallback, useMemo, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { DetailsSection } from "@/components/map/layer-panel/DetailsSection";
import {
  DETAILS_LABELS,
  DOCK_LAYER_GROUPS,
  DOCK_PIVOT_SECTIONS,
  dockGroupDomId,
  type DockLayerGroup,
} from "@/components/map/layer-panel/dock-sections";
import { DockSectionIcon } from "@/components/map/layer-panel/layer-icons";
import { LayerRow } from "@/components/map/layer-panel/LayerRow";
import { useUnreadAlertCount } from "@/hooks/useUnreadAlertCount";
import {
  DEFAULT_LEGEND_CONTEXT,
  type LegendContext,
} from "@/lib/map/layer-legends";
import { LAYER_REGISTRY } from "@/lib/map/layer-registry";
import {
  useClimateDisplayMode,
  useLayerVisibility,
  useSoilDisplayMode,
  useToggleLayer,
} from "@/lib/map/layer-toggle-context";
import { useVegetationStore } from "@/stores/vegetation-store";

/**
 * One category: a disclosure caret, a tri-state group eye, an `n of m` count, its layer rows,
 * and the report filed under it.
 *
 * The caret governs the ROWS only, never the report below them. Two reasons, and they are the
 * same reason twice: showing a layer row costs nothing while mounting a report costs a fistful
 * of warehouse queries, so the list can afford to open by default and the reports cannot; and
 * a collapsed category that still lists its report reads as an index, which is what a reader
 * who has finished picking layers actually wants.
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
  group: DockLayerGroup;
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
    <section
      id={dockGroupDomId(group.key)}
      data-testid={`layer-group-${group.key}`}
      // A hairline between categories, which is what makes a report read as belonging to the
      // group above it rather than to the group below.
      className="border-b border-(--glass-border) pb-1 last:border-b-0"
    >
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
          <DockSectionIcon sectionKey={group.key} className="h-3 w-3 shrink-0 opacity-70" />
          {group.label}{" "}
          {/* The explicit space is not decoration: JSX drops whitespace that spans a newline
              between two children, so without it this button's accessible name ran the two
              together and announced as "Water0 of 5". */}
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
      {group.detailsId !== null && (
        <div className="pl-4.5">
          <DetailsSection id={group.detailsId} label={DETAILS_LABELS[group.detailsId]} />
        </div>
      )}
    </section>
  );
}

/** The unread count, as a chip on the Alerts disclosure. Silent at zero rather than "0". */
function UnreadAlertBadge() {
  const count = useUnreadAlertCount();
  if (count === 0) return null;
  return (
    <span
      className="rounded-full bg-red-500 px-1.5 py-px text-[10px] font-bold leading-none text-white"
      aria-label={`${count} unread`}
    >
      {count > 99 ? "99+" : count}
    </span>
  );
}

/**
 * The dock's body: every layer grouped by category with its report, then the reports that
 * belong to no category.
 *
 * The display modes that change what a layer PAINTS are read here once and handed down, so
 * seventeen rows do not each open their own store subscriptions for the same two fields. Read
 * straight from the vegetation store rather than through `useVegetationDisplayMode`, which
 * also projects the slider's settled day -- a per-mount settle timer that moves nothing on a
 * colour chip.
 */
export function DockSections() {
  const soilDisplayMode = useSoilDisplayMode();
  const climateDisplayMode = useClimateDisplayMode();
  const vegetationMode = useVegetationStore((state) => state.mode);
  const ndviMode = useVegetationStore((state) => state.ndviMode);

  const legendContext = useMemo<LegendContext>(
    () => ({
      ...DEFAULT_LEGEND_CONTEXT,
      vegetationMode,
      ndviMode,
      soilFieldDepth: soilDisplayMode.fieldDepth,
      climateFieldSignal: climateDisplayMode.signal,
      climateFieldVariant: climateDisplayMode.airTemperatureVariant,
    }),
    [
      vegetationMode,
      ndviMode,
      soilDisplayMode.fieldDepth,
      climateDisplayMode.signal,
      climateDisplayMode.airTemperatureVariant,
    ]
  );

  return (
    <div className="flex flex-col gap-1">
      {DOCK_LAYER_GROUPS.map((group) => (
        <LayerGroupSection key={group.key} group={group} legendContext={legendContext} />
      ))}

      {/* Alerts, Teams and Analytics govern no layer, so the registry cannot order them and
          they have no rows to sit under. They keep the dock's one vocabulary -- a caret and a
          name -- rather than becoming a second control style for the same act of disclosure. */}
      <div className="mt-1 flex flex-col gap-0.5 border-t border-(--glass-border) pt-2">
        <h3 className="px-1 text-[10px] font-semibold uppercase tracking-wide text-[hsl(var(--muted-foreground))] opacity-70">
          Workspace
        </h3>
        {DOCK_PIVOT_SECTIONS.map((id) => (
          <DetailsSection
            key={id}
            id={id}
            label={DETAILS_LABELS[id]}
            icon={<DockSectionIcon sectionKey={id} className="h-3.5 w-3.5 shrink-0 opacity-70" />}
            badge={id === "alerts" ? <UnreadAlertBadge /> : undefined}
          />
        ))}
      </div>
    </div>
  );
}
