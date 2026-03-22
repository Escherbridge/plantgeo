"use client";

import { Car, Bike, PersonStanding, Truck } from "lucide-react";
import { useRoutingStore, type TransportMode } from "@/stores/routing-store";
import { cn } from "@/lib/utils";

const MODES: { mode: TransportMode; label: string; icon: React.ReactNode }[] = [
  { mode: "car", label: "Car", icon: <Car className="h-4 w-4" /> },
  { mode: "bike", label: "Bike", icon: <Bike className="h-4 w-4" /> },
  { mode: "pedestrian", label: "Walk", icon: <PersonStanding className="h-4 w-4" /> },
  { mode: "truck", label: "Truck", icon: <Truck className="h-4 w-4" /> },
];

export function ModeSelector() {
  const { transportMode, setTransportMode } = useRoutingStore();

  return (
    <div className="flex gap-1">
      {MODES.map(({ mode, label, icon }) => (
        <button
          key={mode}
          type="button"
          title={label}
          onClick={() => setTransportMode(mode)}
          className={cn(
            "flex flex-1 flex-col items-center gap-1 rounded-md px-2 py-2 text-xs transition-colors",
            transportMode === mode
              ? "bg-emerald-500 text-white"
              : "bg-[hsl(var(--muted))] text-[hsl(var(--muted-foreground))] hover:bg-[hsl(var(--accent))] hover:text-[hsl(var(--accent-foreground))]"
          )}
        >
          {icon}
          <span>{label}</span>
        </button>
      ))}
    </div>
  );
}
