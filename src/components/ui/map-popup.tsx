import * as React from "react";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";

interface MapPopupProps {
  children: React.ReactNode;
  className?: string;
  title?: string;
  onClose?: () => void;
}

const MapPopup = React.forwardRef<HTMLDivElement, MapPopupProps>(
  ({ children, className, title, onClose, ...props }, ref) => {
    return (
      <div
        ref={ref}
        className={cn("relative inline-flex flex-col", className)}
        {...props}
      >
        <div
          className={cn(
            "relative rounded-xl",
            "bg-[hsl(var(--card))] text-[hsl(var(--card-foreground))]",
            "border border-[hsl(var(--border))]",
            "shadow-[var(--shadow-xl)]",
            "min-w-[200px] max-w-[320px]"
          )}
        >
          {(title || onClose) && (
            <MapPopupHeader title={title} onClose={onClose} />
          )}
          {children}
        </div>
        <div
          className={cn(
            "mx-auto h-0 w-0",
            "border-l-[8px] border-r-[8px] border-t-[8px]",
            "border-l-transparent border-r-transparent",
            "border-t-[hsl(var(--border))]"
          )}
        />
      </div>
    );
  }
);
MapPopup.displayName = "MapPopup";

interface MapPopupHeaderProps {
  title?: string;
  onClose?: () => void;
  className?: string;
}

function MapPopupHeader({ title, onClose, className }: MapPopupHeaderProps) {
  return (
    <div
      className={cn(
        "flex items-center justify-between gap-2",
        "border-b border-[hsl(var(--border))] px-4 py-3",
        className
      )}
    >
      {title && (
        <h3 className="text-sm font-semibold text-[hsl(var(--foreground))]">
          {title}
        </h3>
      )}
      {onClose && (
        <button
          onClick={onClose}
          className={cn(
            "ml-auto inline-flex h-6 w-6 items-center justify-center rounded-md",
            "text-[hsl(var(--muted-foreground))]",
            "transition-colors hover:bg-[hsl(var(--muted))] hover:text-[hsl(var(--foreground))]",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--ring))]"
          )}
        >
          <X className="h-4 w-4" />
        </button>
      )}
    </div>
  );
}

interface MapPopupBodyProps {
  children: React.ReactNode;
  className?: string;
}

function MapPopupBody({ children, className }: MapPopupBodyProps) {
  return <div className={cn("px-4 py-3", className)}>{children}</div>;
}

export { MapPopup, MapPopupHeader, MapPopupBody };
export type { MapPopupProps, MapPopupHeaderProps, MapPopupBodyProps };
