"use client";

import { useState } from "react";
import { Layers, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { trpc } from "@/lib/trpc/client";
import { LayerItem } from "./LayerItem";

interface LayerPanelProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function LayerPanel({ open, onOpenChange }: LayerPanelProps) {
  const { data: layers = [], refetch } = trpc.layers.list.useQuery();
  const reorderMutation = trpc.layers.reorder.useMutation();

  const [draggedId, setDraggedId] = useState<string | null>(null);
  const [orderedIds, setOrderedIds] = useState<string[]>([]);

  const orderedLayers = orderedIds.length
    ? orderedIds.map((id) => layers.find((l) => l.id === id)).filter(Boolean)
    : layers;

  function handleDragStart(e: React.DragEvent, id: string) {
    setDraggedId(id);
    e.dataTransfer.effectAllowed = "move";
  }

  function handleDragOver(e: React.DragEvent) {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
  }

  function handleDrop(e: React.DragEvent, targetId: string) {
    e.preventDefault();
    if (!draggedId || draggedId === targetId) return;

    const ids = (orderedIds.length ? orderedIds : layers.map((l) => l.id));
    const from = ids.indexOf(draggedId);
    const to = ids.indexOf(targetId);
    if (from === -1 || to === -1) return;

    const next = [...ids];
    next.splice(from, 1);
    next.splice(to, 0, draggedId);
    setOrderedIds(next);
    setDraggedId(null);
    reorderMutation.mutate({ ids: next });
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="left" onOpenChange={onOpenChange}>
        <SheetHeader>
          <SheetTitle className="flex items-center gap-2">
            <Layers className="h-5 w-5" />
            Layers
          </SheetTitle>
        </SheetHeader>
        <div className="flex flex-col gap-2">
          {orderedLayers.map((layer) =>
            layer ? (
              <LayerItem
                key={layer.id}
                id={layer.id}
                name={layer.name}
                type={layer.type}
                style={(layer.style as Record<string, unknown>) ?? {}}
                onDragStart={handleDragStart}
                onDragOver={handleDragOver}
                onDrop={handleDrop}
              />
            ) : null
          )}
          {layers.length === 0 && (
            <p className="text-center text-sm text-[hsl(var(--muted-foreground))] py-8">
              No layers yet. Upload data to get started.
            </p>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}
