"use client";

import { useState, useRef, useCallback, useEffect, type ReactNode } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";

interface SidePanelProps {
  children: ReactNode;
  title?: ReactNode;
  defaultWidth?: number;
  minWidth?: number;
  maxWidth?: number;
  collapsed?: boolean;
  onCollapsedChange?: (collapsed: boolean) => void;
}

export default function SidePanel({
  children,
  title,
  defaultWidth = 360,
  minWidth = 300,
  maxWidth = 600,
  collapsed: controlledCollapsed,
  onCollapsedChange,
}: SidePanelProps) {
  const [internalCollapsed, setInternalCollapsed] = useState(false);
  const isControlled = controlledCollapsed !== undefined;
  const collapsed = isControlled ? controlledCollapsed : internalCollapsed;

  const [width, setWidth] = useState(defaultWidth);
  const isResizing = useRef(false);
  const panelRef = useRef<HTMLDivElement>(null);

  const toggleCollapsed = useCallback(() => {
    const next = !collapsed;
    if (isControlled) {
      onCollapsedChange?.(next);
    } else {
      setInternalCollapsed(next);
    }
  }, [collapsed, isControlled, onCollapsedChange]);

  const handleResizeStart = useCallback(
    (e: React.PointerEvent) => {
      if (collapsed) return;
      e.preventDefault();
      isResizing.current = true;

      const startX = e.clientX;
      const startWidth = width;

      const onPointerMove = (moveEvent: PointerEvent) => {
        if (!isResizing.current) return;
        const delta = moveEvent.clientX - startX;
        const newWidth = Math.min(maxWidth, Math.max(minWidth, startWidth + delta));
        setWidth(newWidth);
      };

      const onPointerUp = () => {
        isResizing.current = false;
        document.removeEventListener("pointermove", onPointerMove);
        document.removeEventListener("pointerup", onPointerUp);
        document.body.style.cursor = "";
        document.body.style.userSelect = "";
      };

      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";
      document.addEventListener("pointermove", onPointerMove);
      document.addEventListener("pointerup", onPointerUp);
    },
    [collapsed, width, minWidth, maxWidth],
  );

  useEffect(() => {
    return () => {
      isResizing.current = false;
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };
  }, []);

  return (
    <div
      ref={panelRef}
      className={cn(
        "relative flex h-full flex-col border-r transition-[width] duration-300 ease-in-out",
        collapsed && "w-0 overflow-hidden border-r-0",
      )}
      style={{
        width: collapsed ? 0 : width,
        background: "var(--glass-bg)",
        backdropFilter: `blur(var(--glass-blur))`,
        WebkitBackdropFilter: `blur(var(--glass-blur))`,
        borderColor: "var(--glass-border)",
      }}
    >
      {title && (
        <div
          className="flex h-14 shrink-0 items-center border-b px-4 text-sm font-semibold text-[hsl(var(--foreground))]"
          style={{ borderColor: "var(--glass-border)" }}
        >
          {title}
        </div>
      )}

      <div className="flex-1 overflow-y-auto overflow-x-hidden">{children}</div>

      {!collapsed && (
        <div
          className="absolute right-0 top-0 z-10 h-full w-1.5 cursor-col-resize transition-colors hover:bg-[hsl(var(--accent)/0.4)]"
          onPointerDown={handleResizeStart}
        />
      )}

      <button
        onClick={toggleCollapsed}
        className={cn(
          "absolute top-1/2 z-20 flex h-8 w-5 -translate-y-1/2 items-center justify-center rounded-r-md border border-l-0 text-[hsl(var(--muted-foreground))] transition-colors hover:text-[hsl(var(--foreground))]",
          collapsed ? "left-0" : "-right-5",
        )}
        style={{
          background: "var(--glass-bg)",
          backdropFilter: `blur(var(--glass-blur))`,
          WebkitBackdropFilter: `blur(var(--glass-blur))`,
          borderColor: "var(--glass-border)",
        }}
        aria-label={collapsed ? "Expand panel" : "Collapse panel"}
      >
        {collapsed ? <ChevronRight size={14} /> : <ChevronLeft size={14} />}
      </button>
    </div>
  );
}
