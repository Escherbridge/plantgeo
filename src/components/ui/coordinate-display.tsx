"use client";

import { cn } from "@/lib/utils";

interface CoordinateDisplayProps {
  latitude: number;
  longitude: number;
  zoom: number;
  className?: string;
}

function CoordinateDisplay({
  latitude,
  longitude,
  zoom,
  className,
}: CoordinateDisplayProps) {
  return (
    <div
      className={cn(
        "inline-flex items-center gap-3 rounded-lg px-3 py-1.5",
        "bg-[var(--glass-bg)] border border-[var(--glass-border)]",
        "shadow-[var(--shadow-sm)]",
        "[backdrop-filter:blur(var(--glass-blur))]",
        "text-xs text-[hsl(var(--foreground))]",
        className
      )}
    >
      <span className="flex items-center gap-1">
        <span className="text-[hsl(var(--muted-foreground))]">Lat</span>
        <span className="font-mono tabular-nums">{latitude.toFixed(6)}</span>
      </span>
      <span className="h-3 w-px bg-[var(--glass-border)]" />
      <span className="flex items-center gap-1">
        <span className="text-[hsl(var(--muted-foreground))]">Lng</span>
        <span className="font-mono tabular-nums">{longitude.toFixed(6)}</span>
      </span>
      <span className="h-3 w-px bg-[var(--glass-border)]" />
      <span className="flex items-center gap-1">
        <span className="text-[hsl(var(--muted-foreground))]">Z</span>
        <span className="font-mono tabular-nums">{zoom.toFixed(1)}</span>
      </span>
    </div>
  );
}

export { CoordinateDisplay };
export type { CoordinateDisplayProps };
