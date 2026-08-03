"use client";

import { useEffect, useRef, useState } from "react";
import type { Map as MapLibreMap } from "maplibre-gl";
import { HOVERABLE_LAYER_IDS, formatHoverContent, type HoverContent } from "@/lib/map/hover-fields";

interface HoverTooltipProps {
  map: MapLibreMap;
}

interface TooltipState {
  content: HoverContent;
  x: number;
  y: number;
}

const TOOLTIP_OFFSET = 14;
// Fallback size used only for the very first frame a tooltip appears, before
// its real rect has been measured -- keeps the initial position sane.
const FALLBACK_WIDTH = 220;
const FALLBACK_HEIGHT = 80;

/**
 * Shared hover affordance for every visualized dataset: one mousemove listener
 * queries HOVERABLE_LAYER_IDS and renders a lightweight tooltip near the cursor.
 * Sits alongside the richer click popups owned by FireLayer/WaterLayer.
 */
export default function HoverTooltip({ map }: HoverTooltipProps) {
  const [tooltip, setTooltip] = useState<TooltipState | null>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleMouseMove(e: maplibregl.MapMouseEvent) {
      try {
        if (!map.getStyle()) return;
      } catch {
        return;
      }

      // Style switches change which layers exist -- only query ones present now.
      const presentLayerIds = HOVERABLE_LAYER_IDS.filter((id) => map.getLayer(id));
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
          setTooltip({ content, x: e.point.x, y: e.point.y });
          return;
        }
      }

      map.getCanvas().style.cursor = "";
      setTooltip(null);
    }

    function handleMouseOut() {
      map.getCanvas().style.cursor = "";
      setTooltip(null);
    }

    // Re-evaluate on style.load: the previously hovered layer id may no
    // longer exist in the new style, and a stale tooltip would be wrong.
    function handleStyleLoad() {
      setTooltip(null);
    }

    map.on("mousemove", handleMouseMove);
    map.on("mouseout", handleMouseOut);
    map.on("style.load", handleStyleLoad);

    return () => {
      map.off("mousemove", handleMouseMove);
      map.off("mouseout", handleMouseOut);
      map.off("style.load", handleStyleLoad);
      map.getCanvas().style.cursor = "";
    };
  }, [map]);

  if (!tooltip) return null;

  // Flip near viewport edges (using the map container as the positioning
  // bound) so the tooltip never clips outside it.
  const container = map.getContainer();
  const containerWidth = container.clientWidth;
  const containerHeight = container.clientHeight;
  const tooltipWidth = tooltipRef.current?.offsetWidth ?? FALLBACK_WIDTH;
  const tooltipHeight = tooltipRef.current?.offsetHeight ?? FALLBACK_HEIGHT;

  const flipX = tooltip.x + TOOLTIP_OFFSET + tooltipWidth > containerWidth;
  const flipY = tooltip.y + TOOLTIP_OFFSET + tooltipHeight > containerHeight;

  const left = flipX ? tooltip.x - TOOLTIP_OFFSET - tooltipWidth : tooltip.x + TOOLTIP_OFFSET;
  const top = flipY ? tooltip.y - TOOLTIP_OFFSET - tooltipHeight : tooltip.y + TOOLTIP_OFFSET;

  return (
    <div
      ref={tooltipRef}
      className="pointer-events-none absolute z-50 max-w-[240px] rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--background))]/95 px-3 py-2 text-xs text-[hsl(var(--foreground))] shadow-lg backdrop-blur-sm"
      style={{ left, top }}
    >
      <p className="mb-1 font-semibold">{tooltip.content.title}</p>
      {tooltip.content.lines.map((line, i) => (
        <p key={i} className="text-[hsl(var(--muted-foreground))]">
          {line}
        </p>
      ))}
    </div>
  );
}
