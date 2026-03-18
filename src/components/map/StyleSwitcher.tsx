"use client";

import { useMapStore } from "@/stores/map-store";
import { cn } from "@/lib/utils";
import type { MapStyle } from "@/types/map";

const styleOptions: { id: MapStyle; label: string; color: string }[] = [
  { id: "dark", label: "Dark", color: "bg-slate-800" },
  { id: "light", label: "Light", color: "bg-slate-200" },
  { id: "satellite", label: "Satellite", color: "bg-emerald-800" },
];

export default function StyleSwitcher() {
  const { currentStyle, setCurrentStyle } = useMapStore();

  return (
    <div className="flex gap-1.5">
      {styleOptions.map((opt) => (
        <button
          key={opt.id}
          onClick={() => setCurrentStyle(opt.id)}
          title={opt.label}
          className={cn(
            "h-8 w-8 rounded-md border-2 transition-all",
            opt.color,
            currentStyle === opt.id
              ? "border-[hsl(var(--primary))] ring-2 ring-[hsl(var(--primary)/0.3)]"
              : "border-transparent hover:border-[hsl(var(--muted-foreground))]"
          )}
        />
      ))}
    </div>
  );
}
