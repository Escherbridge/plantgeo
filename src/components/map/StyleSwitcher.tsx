"use client";

import { useMapStore } from "@/stores/map-store";
import { cn } from "@/lib/utils";
import type { MapStyle } from "@/types/map";

const styleOptions: { id: MapStyle; label: string; color: string }[] = [
  { id: "dark", label: "Dark", color: "bg-slate-800" },
  { id: "light", label: "Light", color: "bg-slate-200" },
  { id: "satellite", label: "Satellite", color: "bg-emerald-800" },
];

/**
 * The three basemap swatches, now the first control in the manager's View section rather than
 * the left end of a floating bottom toolbar. Per-field selectors: it reads no viewport field,
 * so a whole-store destructure would re-render it on every pan and zoom tick.
 */
export default function StyleSwitcher() {
  const currentStyle = useMapStore((state) => state.currentStyle);
  const setCurrentStyle = useMapStore((state) => state.setCurrentStyle);

  return (
    <div className="flex shrink-0 gap-1.5">
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
          // bigger colour chip than its neighbours: the visible square stays h-7 w-7 and is
          // centred inside a larger tap target, the same "icon smaller than its button"
          // proportion every other control in the manager uses.
          className="group flex h-7 w-7 shrink-0 items-center justify-center rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--ring))] max-sm:h-11 max-sm:w-11"
        >
          <span
            aria-hidden="true"
            className={cn(
              "h-7 w-7 rounded-md border-2 transition-all",
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
