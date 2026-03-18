import { cn } from "@/lib/utils";

interface ZoomIndicatorProps {
  zoom: number;
  className?: string;
}

function ZoomIndicator({ zoom, className }: ZoomIndicatorProps) {
  return (
    <div
      className={cn(
        "inline-flex items-center rounded-full px-2.5 py-1",
        "bg-[var(--glass-bg)] border border-[var(--glass-border)]",
        "shadow-[var(--shadow-sm)]",
        "[backdrop-filter:blur(var(--glass-blur))]",
        "text-xs font-medium text-[hsl(var(--foreground))]",
        className
      )}
    >
      <span className="text-[hsl(var(--muted-foreground))]">Z:&nbsp;</span>
      <span className="font-mono tabular-nums">{zoom.toFixed(1)}</span>
    </div>
  );
}

export { ZoomIndicator };
export type { ZoomIndicatorProps };
