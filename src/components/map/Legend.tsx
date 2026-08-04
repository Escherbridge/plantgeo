"use client";

import { Eye, EyeOff } from "lucide-react";
import { Button } from "@/components/ui/button";
import { trpc } from "@/lib/trpc/client";
import { useLayerStore } from "@/stores/layer-store";
import { toggleIdForWarehouseLayerName } from "@/lib/map/layer-registry";
import { useActiveLayerToggles, useToggleLayer } from "@/lib/map/layer-toggle-context";
import { stylePresets } from "@/lib/map/layer-styles";

export function Legend() {
  const { data: layers = [] } = trpc.layers.list.useQuery();
  const { legendVisible, toggleLegend, styleOverrides } = useLayerStore();
  // The legend is a consumer of the toggle vocabulary, never a second source of it: it
  // translates a geo.layers name to the toggle that renders it, and disables the rest.
  const activeLayers = useActiveLayerToggles();
  const toggleLayer = useToggleLayer();

  if (layers.length === 0) return null;

  return (
    <div className="absolute bottom-8 right-4 z-10 min-w-40 rounded-(--radius) border border-[hsl(var(--border))] bg-[hsl(var(--card))]/90 p-3 shadow-lg backdrop-blur-sm">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-xs font-semibold uppercase tracking-wide text-[hsl(var(--muted-foreground))]">
          Legend
        </span>
        <Button variant="ghost" size="icon" className="h-6 w-6" onClick={toggleLegend}>
          {legendVisible ? <Eye className="h-3.5 w-3.5" /> : <EyeOff className="h-3.5 w-3.5" />}
        </Button>
      </div>

      {legendVisible && (
        <ul className="flex flex-col gap-1.5">
          {layers.map((layer) => {
            const override = styleOverrides.get(layer.id);
            const styleKey = (layer.style as Record<string, string> | null)?.preset;
            const preset = styleKey ? stylePresets[styleKey] : null;
            const fillColor =
              override?.fillColor ?? preset?.fillColor ?? "#6b7280";
            const toggleId = toggleIdForWarehouseLayerName(layer.name);
            const isVisible = toggleId !== null && activeLayers.includes(toggleId);

            return (
              <li key={layer.id} className="flex items-center gap-2">
                <button
                  onClick={() => toggleId && toggleLayer(toggleId)}
                  disabled={toggleId === null}
                  className="flex items-center gap-2 text-left disabled:cursor-not-allowed"
                  title={
                    toggleId === null
                      ? "No renderer for this layer"
                      : isVisible
                        ? "Hide layer"
                        : "Show layer"
                  }
                >
                  <span
                    className="inline-block h-3 w-3 shrink-0 rounded-sm border border-black/10"
                    style={{
                      backgroundColor: fillColor,
                      opacity: isVisible ? 1 : 0.4,
                    }}
                  />
                  <span
                    className={`text-xs ${
                      isVisible
                        ? "text-[hsl(var(--foreground))]"
                        : "text-[hsl(var(--muted-foreground))]"
                    }`}
                  >
                    {layer.name}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
