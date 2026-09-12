"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { Map as MapLibreMap, PointLike } from "maplibre-gl";
import { isScalarFieldInspectionAllowed, subscribeScalarFieldInspection } from "@/lib/map/scalar-field-inspection";
import {
  HOVERABLE_LAYER_IDS,
  TOOLTIP_TAP_LAYER_IDS,
  formatHoverContent,
  type HoverContent,
} from "@/lib/map/hover-fields";

interface HoverTooltipProps {
  map: MapLibreMap;
}

interface TooltipState {
  content: HoverContent;
  layerId: string;
  x: number;
  y: number;
  /**
   * Set by a tap, never by a hover. A pinned tooltip survives the `mousemove` handler below
   * (there is nothing to survive on a touchscreen -- taps fire no `mousemove` at all -- but a
   * hybrid device that DOES fire one must not have its tap-opened caption yanked away the
   * instant the pointer so much as twitches) and is cleared only by another tap: on the same
   * feature (toggle off), on a different one (replace), or on empty ground (dismiss).
   */
  pinned: boolean;
}

const TOOLTIP_OFFSET = 14;
// Fallback size used only for the very first frame a tooltip appears, before
// its real rect has been measured -- keeps the initial position sane.
const FALLBACK_WIDTH = 220;
const FALLBACK_HEIGHT = 80;

/**
 * Half-width, in CSS pixels, of the square a TAP hit-tests against instead of the bare point
 * `mousemove` uses. A mouse cursor is a pixel; a fingertip is not, and the circle layers this
 * tooltip covers were sized for the former -- `weather-temperature` and the coarse aggregate
 * cells run as small as a 2-4px radius at their lowest zoom anchor (`WeatherLayer.tsx`,
 * `WaterLayer.tsx`). Widening the query box rather than the painted radius keeps the rendered
 * geometry exactly as designed; only what counts as "on it" changes.
 */
const TAP_HIT_TEST_PADDING_PX = 12;

/** True on a device whose PRIMARY pointer has no hover state -- a tap, not a click. */
function isCoarsePointer(): boolean {
  // Optional-called: jsdom under vitest implements no `matchMedia` (see LayerPanel.tsx's
  // identical guard), and this must degrade to "assume a mouse" rather than throw.
  return window.matchMedia?.("(pointer: coarse)").matches === true;
}

function tapHitTestGeometry(point: { x: number; y: number }): PointLike | [PointLike, PointLike] {
  if (!isCoarsePointer()) return [point.x, point.y];
  return [
    [point.x - TAP_HIT_TEST_PADDING_PX, point.y - TAP_HIT_TEST_PADDING_PX],
    [point.x + TAP_HIT_TEST_PADDING_PX, point.y + TAP_HIT_TEST_PADDING_PX],
  ];
}

/** Two captions are the same feature if they would read identically; there is no feature id every layer here carries. */
function sameContent(a: HoverContent, b: HoverContent): boolean {
  return a.title === b.title && a.lines.length === b.lines.length && a.lines.every((line, i) => line === b.lines[i]);
}

/**
 * Shared hover-and-tap affordance for every visualized dataset. A `mousemove` listener queries
 * `HOVERABLE_LAYER_IDS` and renders a lightweight tooltip near the cursor for a fine pointer;
 * a `click` listener queries `TOOLTIP_TAP_LAYER_IDS` (with a padded hit box) and PINS the same
 * tooltip open for a coarse one, because a tap fires no `mousemove` for it to ever appear from
 * otherwise. Sits alongside the richer click popups owned by FireLayer/WaterLayer, which own the
 * six ids `TOOLTIP_TAP_LAYER_IDS` excludes.
 */
export default function HoverTooltip({ map }: HoverTooltipProps) {
  const [tooltip, setTooltip] = useState<TooltipState | null>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);
  // Read by `handleMouseMove` without joining the effect's deps -- see the note on `pinned`
  // above for why a hover pass must not clear what a tap just pinned.
  const pinnedRef = useRef(false);

  useEffect(() => {
    function handleMouseMove(e: maplibregl.MapMouseEvent) {
      // A tap just pinned this tooltip open; a live hover pass must not immediately clear it --
      // see the `pinned` field's doc comment. On a genuine coarse pointer nothing here fires a
      // real `mousemove` anyway, but a hybrid device (a touch laptop with a trackpad) might.
      if (pinnedRef.current) return;

      try {
        if (!map.getStyle()) return;
      } catch {
        return;
      }

      // Style switches change which layers exist -- only query ones present now.
      const presentLayerIds = HOVERABLE_LAYER_IDS.filter((id) => map.getLayer(id) && isScalarFieldInspectionAllowed(map, id));
      const features =
        presentLayerIds.length > 0
          ? map.queryRenderedFeatures(e.point, { layers: presentLayerIds })
          : [];

      for (const feature of features) {
        const layerId = feature.layer?.id;
        if (!layerId) continue;
        const content = formatHoverContent(layerId, (feature.properties ?? {}) as Record<string, unknown>);
        if (content) {
          map.getCanvas().style.cursor = "pointer";
          setTooltip({ content, layerId, x: e.point.x, y: e.point.y, pinned: false });
          return;
        }
      }

      map.getCanvas().style.cursor = "";
      setTooltip(null);
    }

    function handleMouseOut() {
      if (pinnedRef.current) return;
      map.getCanvas().style.cursor = "";
      setTooltip(null);
    }

    /**
     * The tap counterpart to `handleMouseMove`, for `TOOLTIP_TAP_LAYER_IDS` -- the thirteen
     * layers whose only other caption path is a hover that a touchscreen never fires. A no-op on
     * a fine pointer: hover already covers a mouse, and this must change nothing about that
     * device's behaviour. Pins the tooltip open rather than showing it only while the pointer is
     * down, because there is no touch equivalent of "still hovering" to keep it open with.
     */
    function handleClick(e: maplibregl.MapMouseEvent) {
      if (!isCoarsePointer()) return;
      try {
        if (!map.getStyle()) return;
      } catch {
        return;
      }

      const presentLayerIds = TOOLTIP_TAP_LAYER_IDS.filter((id) => map.getLayer(id) && isScalarFieldInspectionAllowed(map, id));
      const features =
        presentLayerIds.length > 0
          ? map.queryRenderedFeatures(tapHitTestGeometry(e.point), { layers: presentLayerIds })
          : [];

      for (const feature of features) {
        const layerId = feature.layer?.id;
        if (!layerId) continue;
        const content = formatHoverContent(layerId, (feature.properties ?? {}) as Record<string, unknown>);
        if (content) {
          setTooltip((previous) => {
            // Tapping the same pinned feature again is how a touch reader dismisses it -- there
            // is no "move the mouse away" for them to do instead.
            if (previous?.pinned && sameContent(previous.content, content)) {
              pinnedRef.current = false;
              return null;
            }
            pinnedRef.current = true;
            return { content, layerId, x: e.point.x, y: e.point.y, pinned: true };
          });
          return;
        }
      }

      // Tapped the map with nothing under the padded box: dismiss whatever was pinned, exactly
      // as moving the mouse off a feature does for a hover tooltip.
      pinnedRef.current = false;
      setTooltip(null);
    }

    // Re-evaluate on style.load: the previously hovered layer id may no
    // longer exist in the new style, and a stale tooltip would be wrong.
    function handleStyleLoad() {
      pinnedRef.current = false;
      setTooltip(null);
    }

    function handleSourceData(event: maplibregl.MapSourceDataEvent) {
      if (event.sourceId !== "vegetation-ndvi-cells" || event.sourceDataType !== "content") return;
      setTooltip(previous => {
        if (previous?.layerId !== "vegetation-ndvi-cells-fill") return previous;
        pinnedRef.current = false;
        map.getCanvas().style.cursor = "";
        return null;
      });
    }

    map.on("mousemove", handleMouseMove);
    map.on("mouseout", handleMouseOut);
    map.on("click", handleClick);
    map.on("style.load", handleStyleLoad);
    map.on("sourcedata", handleSourceData);
    const unsubscribeInspection = subscribeScalarFieldInspection(map, () => {
      setTooltip(previous => {
        if (!previous || isScalarFieldInspectionAllowed(map, previous.layerId)) return previous;
        pinnedRef.current = false;
        map.getCanvas().style.cursor = "";
        return null;
      });
    });

    return () => {
      unsubscribeInspection();
      map.off("mousemove", handleMouseMove);
      map.off("mouseout", handleMouseOut);
      map.off("click", handleClick);
      map.off("style.load", handleStyleLoad);
      map.off("sourcedata", handleSourceData);
      map.getCanvas().style.cursor = "";
    };
  }, [map]);

  // Measure the current caption before paint; see AGENTS.md §vegetation-scalar-field.
  useLayoutEffect(() => {
    const element = tooltipRef.current;
    if (!tooltip || !element) return;
    const container = map.getContainer();
    const width = element.offsetWidth || FALLBACK_WIDTH;
    const height = element.offsetHeight || FALLBACK_HEIGHT;
    const flipX = tooltip.x + TOOLTIP_OFFSET + width > container.clientWidth;
    const flipY = tooltip.y + TOOLTIP_OFFSET + height > container.clientHeight;
    const left = flipX ? tooltip.x - TOOLTIP_OFFSET - width : tooltip.x + TOOLTIP_OFFSET;
    const top = flipY ? tooltip.y - TOOLTIP_OFFSET - height : tooltip.y + TOOLTIP_OFFSET;
    element.style.left = `${Math.max(0, Math.min(left, container.clientWidth - width))}px`;
    element.style.top = `${Math.max(0, Math.min(top, container.clientHeight - height))}px`;
  }, [map, tooltip]);

  if (!tooltip) return null;

  return (
    <div
      ref={tooltipRef}
      className="pointer-events-none absolute z-50 max-w-[240px] rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--background))]/95 px-3 py-2 text-xs text-[hsl(var(--foreground))] shadow-lg backdrop-blur-sm"
      style={{ left: 0, top: 0, width: "max-content", maxWidth: "min(240px, 100%)" }}
    >
      {tooltip.pinned && (
        // pointer-events-auto against the container's pointer-events-none: dismissing a pinned
        // tap tooltip must not require landing the next tap on empty ground, which a dense
        // cluster of cells can make genuinely hard to find. 44px, matching every other mobile
        // tap target on this map (`dock-disclosure.ts`'s `max-sm:min-h-11` convention).
        <button
          type="button"
          onClick={() => {
            pinnedRef.current = false;
            setTooltip(null);
          }}
          aria-label="Close"
          className="pointer-events-auto absolute right-0 top-0 flex h-11 w-11 items-center justify-center text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--foreground))]"
        >
          <svg viewBox="0 0 16 16" className="h-3.5 w-3.5" aria-hidden="true">
            <path
              d="M3 3l10 10M13 3L3 13"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
              fill="none"
            />
          </svg>
        </button>
      )}
      <p className="mb-1 pr-6 font-semibold">{tooltip.content.title}</p>
      {tooltip.content.lines.map((line, i) => (
        <p key={i} className="text-[hsl(var(--muted-foreground))]">
          {line}
        </p>
      ))}
    </div>
  );
}
