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
          // The visible content is an aria-hidden colour swatch, so without this the
          // button has no accessible name at all.
          aria-label={`${opt.label} basemap`}
          aria-pressed={currentStyle === opt.id}
          // The hit area grows to the 44px mobile minimum without the swatch reading as a
          // bigger colour chip than its neighbours: the visible square stays h-8 w-8 and is
          // centred inside a larger tap target, the same "icon smaller than its button"
          // proportion the icon buttons elsewhere in this toolbar already use.
          className="group flex h-8 w-8 shrink-0 items-center justify-center rounded-md max-sm:h-11 max-sm:w-11"
        >
          <span
            aria-hidden="true"
            className={cn(
              "h-8 w-8 rounded-md border-2 transition-all",
              opt.color,
              currentStyle === opt.id
                ? "border-[hsl(var(--primary))] ring-2 ring-[hsl(var(--primary)/0.3)]"
                : "border-transparent group-hover:border-[hsl(var(--muted-foreground))]"
            )}
          />
        </button>
      ))}
    </div>
  );
}
