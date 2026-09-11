"use client";

import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { ChevronUp } from "lucide-react";
import { LayerSwatch } from "@/components/map/layer-panel/LayerSwatch";
import { LegendBlockView } from "@/components/map/layer-panel/LegendBlockView";
import { GROUP_LABELS } from "@/components/map/layer-panel/dock-sections";
import {
  activeLegendEntries,
  type LegendContext,
  type LegendEntry,
} from "@/lib/map/layer-legends";
import { LAYER_REGISTRY, layerLabel } from "@/lib/map/layer-registry";
import {
  useClimateDisplayMode,
  useLayerVisibility,
  useSoilDisplayMode,
} from "@/lib/map/layer-toggle-context";
import { useVegetationStore } from "@/stores/vegetation-store";

/** One category's worth of drawn layers, in registry order within the category. */
interface LegendCategory {
  label: string;
  entries: LegendEntry[];
}

/**
 * Groups the drawn layers under the same category names the layer tree uses.
 *
 * The taxonomy is read off the registry rather than restated, so a layer sits under the same
 * heading in the collapsed legend and in the open manager. Categories appear in the order their
 * first drawn layer does, which is registry order -- the same guarantee `activeLegendEntries`
 * already gives, so nothing reshuffles as toggles come and go.
 */
function groupByCategory(entries: readonly LegendEntry[]): LegendCategory[] {
  const categories: LegendCategory[] = [];
  const byLabel = new Map<string, LegendCategory>();
  for (const entry of entries) {
    const label = GROUP_LABELS[LAYER_REGISTRY[entry.toggleId].panelId];
    let category = byLabel.get(label);
    if (category === undefined) {
      category = { label, entries: [] };
      byLabel.set(label, category);
      categories.push(category);
    }
    category.entries.push(entry);
  }
  return categories;
}

/** Active-layer chips and their complete legend; see src/components/map/AGENTS.md. */
export function LayerLegend() {
  const layerVisibility = useLayerVisibility();
  const soilDisplayMode = useSoilDisplayMode();
  const climateDisplayMode = useClimateDisplayMode();
  // The two vegetation display fields are read from the store directly rather than through
  // useVegetationDisplayMode: that hook also projects the slider's settled day, which costs a
  // store subscription and a settle timer per mount and moves nothing on this card.
  const vegetationMode = useVegetationStore((state) => state.mode);
  const ndviMode = useVegetationStore((state) => state.ndviMode);

  const [isPinnedOpen, setIsPinnedOpen] = useState(false);
  const [isPointerOrFocusOpen, setIsPointerOrFocusOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const taxonomyId = useId();

  const legendContext = useMemo<LegendContext>(
    () => ({
      vegetationMode,
      ndviMode,
      soilFieldDepth: soilDisplayMode.fieldDepth,
      climateRenderForms: climateDisplayMode.renderForms,
      climateFieldVariant: climateDisplayMode.airTemperatureVariant,
    }),
    [
      vegetationMode,
      ndviMode,
      soilDisplayMode.fieldDepth,
      climateDisplayMode.renderForms,
      climateDisplayMode.airTemperatureVariant,
    ]
  );

  const entries = useMemo(
    () => activeLegendEntries(layerVisibility, legendContext),
    [layerVisibility, legendContext]
  );
  const categories = useMemo(() => groupByCategory(entries), [entries]);

  const isTaxonomyOpen = isPinnedOpen || isPointerOrFocusOpen;

  const closeTaxonomy = useCallback(() => {
    setIsPinnedOpen(false);
    setIsPointerOrFocusOpen(false);
  }, []);

  // Escape is the way out of a pinned taxonomy for a reader who opened it from the keyboard,
  // where there is no "move the pointer away".
  useEffect(() => {
    if (!isTaxonomyOpen) return;
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") closeTaxonomy();
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isTaxonomyOpen, closeTaxonomy]);

  // The map opens with every layer off, so this earns its corner only once something is drawn
  // -- and a layer that paints nothing contributes no entry either.
  if (entries.length === 0) return null;

  return (
    <div
      ref={containerRef}
      data-testid="layer-legend"
      className="pointer-events-auto relative flex min-w-0 flex-col"
      onPointerEnter={(event) => {
        // Touch reports itself as a pointer enter that never leaves, which would pin the panel
        // open with no way back; a touch is a click here, and the click handler owns it.
        if (event.pointerType === "touch") return;
        setIsPointerOrFocusOpen(true);
      }}
      onPointerLeave={() => setIsPointerOrFocusOpen(false)}
      onFocus={() => setIsPointerOrFocusOpen(true)}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
          setIsPointerOrFocusOpen(false);
        }
      }}
    >
      {isTaxonomyOpen && (
        <section
          id={taxonomyId}
          data-testid="layer-legend-taxonomy"
          aria-label="Map legend"
          // Opens upward, because the row it belongs to sits at the bottom edge of the map.
          // `scrollbar-hidden` keeps the painted chrome off a panel that is mostly short and
          // occasionally tall; it still scrolls, and still takes the arrow keys.
          className="absolute bottom-full left-0 mb-2 flex max-h-[min(60vh,26rem)] w-[min(20rem,calc(100vw-2rem))] flex-col gap-3 overflow-y-auto overscroll-contain scrollbar-hidden rounded-(--radius) border border-(--glass-border) bg-(--glass-bg) p-3 shadow-(--shadow-lg) [backdrop-filter:blur(var(--glass-blur))]"
          tabIndex={-1}
        >
          {categories.map((category) => (
            <div key={category.label} className="flex flex-col gap-2">
              <h3 className="text-[10px] font-semibold uppercase tracking-wide text-[hsl(var(--muted-foreground))]">
                {category.label}
              </h3>
              {category.entries.map((entry) => (
                <div
                  key={entry.toggleId}
                  data-testid={`legend-entry-${entry.toggleId}`}
                  className="flex flex-col gap-1.5 pl-1"
                >
                  <h4 className="text-xs font-semibold text-[hsl(var(--foreground))]">
                    {entry.spec.title}
                  </h4>
                  {entry.spec.blocks.map((block, blockIndex) => (
                    <LegendBlockView key={blockIndex} block={block} />
                  ))}
                </div>
              ))}
            </div>
          ))}
        </section>
      )}

      <button
        type="button"
        data-testid="layer-legend-chips"
        aria-expanded={isTaxonomyOpen}
        aria-controls={isTaxonomyOpen ? taxonomyId : undefined}
        // The chips ARE the accessible name: a reader hears exactly the list they can see, and
        // the taxonomy behind it is the same list with its colours explained.
        aria-label={`Map legend: ${entries.map((entry) => layerLabel(entry.toggleId)).join(", ")}`}
        onClick={() => setIsPinnedOpen((pinned) => !pinned)}
        // One row, scrolling sideways with no painted scrollbar. `min-w-0` is what lets it
        // shrink inside the rail's flex row instead of pushing the manager button off screen.
        className="flex min-h-8 min-w-0 items-center gap-1.5 overflow-x-auto overscroll-x-contain scrollbar-hidden rounded-xl border border-(--glass-border) bg-(--glass-bg) px-2 py-1.5 shadow-(--shadow-lg) [backdrop-filter:blur(var(--glass-blur))] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[hsl(var(--ring))] max-sm:min-h-11"
      >
        <ChevronUp
          aria-hidden="true"
          className={[
            "h-3 w-3 shrink-0 text-[hsl(var(--muted-foreground))] transition-transform",
            isTaxonomyOpen ? "rotate-180" : "",
          ].join(" ")}
        />
        {entries.map((entry) => (
          <span
            key={entry.toggleId}
            data-testid={`legend-chip-${entry.toggleId}`}
            className="flex shrink-0 items-center gap-1.5 rounded-md bg-[hsl(var(--muted)/0.4)] px-1.5 py-0.5 text-[11px] text-[hsl(var(--foreground))]"
          >
            <LayerSwatch layerId={entry.toggleId} spec={entry.spec} />
            <span className="whitespace-nowrap">{layerLabel(entry.toggleId)}</span>
          </span>
        ))}
      </button>
    </div>
  );
}
