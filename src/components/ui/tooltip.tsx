"use client";

import * as React from "react";
import { cn } from "@/lib/utils";

interface TooltipContextValue {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  triggerRef: React.RefObject<HTMLElement | null>;
}

const TooltipContext = React.createContext<TooltipContextValue | null>(null);

function useTooltipContext() {
  const ctx = React.useContext(TooltipContext);
  if (!ctx) throw new Error("Tooltip compound components must be used within <Tooltip>");
  return ctx;
}

interface TooltipProps {
  children: React.ReactNode;
  delayDuration?: number;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
}

function Tooltip({
  children,
  delayDuration = 300,
  open: controlledOpen,
  onOpenChange,
}: TooltipProps) {
  const [internalOpen, setInternalOpen] = React.useState(false);
  const open = controlledOpen ?? internalOpen;
  const handleOpenChange = onOpenChange ?? setInternalOpen;
  const triggerRef = React.useRef<HTMLElement>(null);
  const timeoutRef = React.useRef<ReturnType<typeof setTimeout>>(undefined);

  const showWithDelay = React.useCallback(() => {
    timeoutRef.current = setTimeout(() => handleOpenChange(true), delayDuration);
  }, [delayDuration, handleOpenChange]);

  const hide = React.useCallback(() => {
    clearTimeout(timeoutRef.current);
    handleOpenChange(false);
  }, [handleOpenChange]);

  React.useEffect(() => {
    return () => clearTimeout(timeoutRef.current);
  }, []);

  return (
    <TooltipContext.Provider value={{ open, onOpenChange: handleOpenChange, triggerRef }}>
      <div
        className="relative inline-flex"
        onMouseEnter={showWithDelay}
        onMouseLeave={hide}
        onFocus={showWithDelay}
        onBlur={hide}
      >
        {children}
      </div>
    </TooltipContext.Provider>
  );
}

interface TooltipTriggerProps extends React.HTMLAttributes<HTMLSpanElement> {
  asChild?: boolean;
}

const TooltipTrigger = React.forwardRef<HTMLSpanElement, TooltipTriggerProps>(
  ({ asChild, children, ...props }, ref) => {
    if (asChild && React.isValidElement(children)) {
      return React.cloneElement(children as React.ReactElement<Record<string, unknown>>, {
        ref,
        tabIndex: 0,
        ...props,
      });
    }

    return (
      <span ref={ref} tabIndex={0} {...props}>
        {children}
      </span>
    );
  }
);
TooltipTrigger.displayName = "TooltipTrigger";

interface TooltipContentProps extends React.HTMLAttributes<HTMLDivElement> {
  side?: "top" | "bottom" | "left" | "right";
  sideOffset?: number;
}

const TooltipContent = React.forwardRef<HTMLDivElement, TooltipContentProps>(
  ({ className, side = "top", sideOffset = 6, children, ...props }, ref) => {
    const { open } = useTooltipContext();

    if (!open) return null;

    const positionClasses = {
      top: "bottom-full left-1/2 -translate-x-1/2 mb-0",
      bottom: "top-full left-1/2 -translate-x-1/2 mt-0",
      left: "right-full top-1/2 -translate-y-1/2 mr-0",
      right: "left-full top-1/2 -translate-y-1/2 ml-0",
    }[side];

    const arrowClasses = {
      top: "left-1/2 -translate-x-1/2 -bottom-1 border-l-transparent border-r-transparent border-b-transparent border-t-[hsl(var(--foreground))]",
      bottom: "left-1/2 -translate-x-1/2 -top-1 border-l-transparent border-r-transparent border-t-transparent border-b-[hsl(var(--foreground))]",
      left: "top-1/2 -translate-y-1/2 -right-1 border-t-transparent border-b-transparent border-r-transparent border-l-[hsl(var(--foreground))]",
      right: "top-1/2 -translate-y-1/2 -left-1 border-t-transparent border-b-transparent border-l-transparent border-r-[hsl(var(--foreground))]",
    }[side];

    const offsetStyle: React.CSSProperties = {
      top: { marginBottom: `${sideOffset}px` },
      bottom: { marginTop: `${sideOffset}px` },
      left: { marginRight: `${sideOffset}px` },
      right: { marginLeft: `${sideOffset}px` },
    }[side];

    return (
      <div
        ref={ref}
        role="tooltip"
        className={cn(
          "absolute z-50 max-w-[280px] rounded-[calc(var(--radius)-2px)] bg-[hsl(var(--foreground))] px-3 py-1.5 text-xs text-[hsl(var(--background))] shadow-md animate-in fade-in-0 zoom-in-95",
          positionClasses,
          className
        )}
        style={offsetStyle}
        {...props}
      >
        {children}
        <span
          className={cn(
            "absolute h-0 w-0 border-4 border-solid",
            arrowClasses
          )}
        />
      </div>
    );
  }
);
TooltipContent.displayName = "TooltipContent";

export { Tooltip, TooltipTrigger, TooltipContent };
