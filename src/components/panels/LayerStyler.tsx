"use client";

import { useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Slider } from "@/components/ui/slider";
import { stylePresets, type LayerStyle } from "@/lib/map/layer-styles";
import { useLayerStore } from "@/stores/layer-store";

interface LayerStylerProps {
  layerId: string;
  layerName: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function LayerStyler({ layerId, layerName, open, onOpenChange }: LayerStylerProps) {
  const { styleOverrides, setStyleOverride } = useLayerStore();

  const existing = styleOverrides.get(layerId) ?? {};
  const [fillColor, setFillColor] = useState<string>(
    typeof existing.fillColor === "string" ? existing.fillColor : "#6b7280"
  );
  const [strokeColor, setStrokeColor] = useState<string>(
    typeof existing.strokeColor === "string" ? existing.strokeColor : "#4b5563"
  );
  const [strokeWidth, setStrokeWidth] = useState<number>(
    typeof existing.strokeWidth === "number" ? existing.strokeWidth : 1
  );
  const [opacity, setOpacity] = useState<number>(
    typeof existing.opacity === "number" ? existing.opacity * 100 : 70
  );

  function applyPreset(key: string) {
    const preset = stylePresets[key];
    if (!preset) return;
    if (preset.fillColor) setFillColor(preset.fillColor);
    if (preset.strokeColor) setStrokeColor(preset.strokeColor);
    if (preset.strokeWidth !== undefined) setStrokeWidth(preset.strokeWidth);
    if (preset.opacity !== undefined) setOpacity(preset.opacity * 100);
  }

  function handleApply() {
    const style: LayerStyle = {
      fillColor,
      strokeColor,
      strokeWidth,
      opacity: opacity / 100,
    };
    setStyleOverride(layerId, style);
    onOpenChange(false);
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent onOpenChange={onOpenChange}>
        <DialogHeader>
          <DialogTitle>Style: {layerName}</DialogTitle>
        </DialogHeader>

        <div className="flex flex-col gap-4 py-2">
          <div className="flex gap-2">
            {Object.keys(stylePresets).map((key) => (
              <button
                key={key}
                onClick={() => applyPreset(key)}
                className="rounded-(--radius) border border-[hsl(var(--border))] px-2 py-1 text-xs hover:bg-[hsl(var(--accent))]"
              >
                {key}
              </button>
            ))}
          </div>

          <div className="flex items-center gap-3">
            <label className="w-28 text-sm">Fill color</label>
            <input
              type="color"
              value={fillColor}
              onChange={(e) => setFillColor(e.target.value)}
              className="h-8 w-12 cursor-pointer rounded border border-[hsl(var(--border))]"
            />
            <span className="text-xs text-[hsl(var(--muted-foreground))]">{fillColor}</span>
          </div>

          <div className="flex items-center gap-3">
            <label className="w-28 text-sm">Stroke color</label>
            <input
              type="color"
              value={strokeColor}
              onChange={(e) => setStrokeColor(e.target.value)}
              className="h-8 w-12 cursor-pointer rounded border border-[hsl(var(--border))]"
            />
            <span className="text-xs text-[hsl(var(--muted-foreground))]">{strokeColor}</span>
          </div>

          <div className="flex items-center gap-3">
            <label className="w-28 text-sm">Stroke width</label>
            <Slider
              min={0}
              max={10}
              step={0.5}
              value={strokeWidth}
              onValueChange={setStrokeWidth}
              className="flex-1"
            />
            <span className="w-8 text-right text-xs">{strokeWidth}</span>
          </div>

          <div className="flex items-center gap-3">
            <label className="w-28 text-sm">Opacity</label>
            <Slider
              min={0}
              max={100}
              step={1}
              value={opacity}
              onValueChange={setOpacity}
              className="flex-1"
            />
            <span className="w-8 text-right text-xs">{opacity}%</span>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={handleApply}>Apply</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
