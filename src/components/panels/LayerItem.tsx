"use client";

import { Eye, EyeOff, GripVertical } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Slider } from "@/components/ui/slider";
import { useMapStore } from "@/stores/map-store";
import { useLayerStore } from "@/stores/layer-store";

interface LayerItemProps {
  id: string;
  name: string;
  type: string;
  style: Record<string, unknown>;
  onDragStart: (e: React.DragEvent, id: string) => void;
  onDragOver: (e: React.DragEvent) => void;
  onDrop: (e: React.DragEvent, id: string) => void;
}

export function LayerItem({
  id,
  name,
  type,
  style,
  onDragStart,
  onDragOver,
  onDrop,
}: LayerItemProps) {
  const { activeLayers, toggleLayer } = useMapStore();
  const { styleOverrides, setStyleOverride } = useLayerStore();

  const isVisible = activeLayers.includes(id);
  const currentStyle = styleOverrides.get(id) ?? style;
  const opacity = typeof currentStyle?.opacity === "number" ? currentStyle.opacity * 100 : 100;

  function handleOpacity(value: number) {
    setStyleOverride(id, { ...currentStyle, opacity: value / 100 });
  }

  return (
    <div
      draggable
      onDragStart={(e) => onDragStart(e, id)}
      onDragOver={onDragOver}
      onDrop={(e) => onDrop(e, id)}
      className="flex items-center gap-2 rounded-(--radius) border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-2"
    >
      <GripVertical className="h-4 w-4 shrink-0 cursor-grab text-[hsl(var(--muted-foreground))]" />
      <Button
        variant="ghost"
        size="icon"
        className="h-7 w-7 shrink-0"
        onClick={() => toggleLayer(id)}
        aria-label={isVisible ? "Hide layer" : "Show layer"}
      >
        {isVisible ? <Eye className="h-4 w-4" /> : <EyeOff className="h-4 w-4" />}
      </Button>
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium">{name}</p>
        <p className="text-xs text-[hsl(var(--muted-foreground))]">{type}</p>
      </div>
      <Slider
        min={0}
        max={100}
        step={1}
        value={opacity}
        onValueChange={handleOpacity}
        className="w-20"
      />
    </div>
  );
}
