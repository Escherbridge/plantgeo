"use client";

import { Eye, EyeOff } from "lucide-react";
import { Button } from "@/components/ui/button";
import { trpc } from "@/lib/trpc/client";
import { useLayerStore } from "@/stores/layer-store";
import { useMapStore } from "@/stores/map-store";
import { stylePresets } from "@/lib/map/layer-styles";

export function Legend() {
  const { data: layers = [] } = trpc.layers.list.useQuery();
  const { legendVisible, toggleLegend, styleOverrides } = useLayerStore();
  const { activeLayers, toggleLayer } = useMapStore();

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
            const isVisible = activeLayers.includes(layer.id);

            return (
              <li key={layer.id} className="flex items-center gap-2">
                <button
                  onClick={() => toggleLayer(layer.id)}
                  className="flex items-center gap-2 text-left"
                  title={isVisible ? "Hide layer" : "Show layer"}
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
