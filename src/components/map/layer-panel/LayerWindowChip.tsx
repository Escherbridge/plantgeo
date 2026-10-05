"use client";

import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import { History } from "lucide-react";
import type { LayerToggleId } from "@/lib/map/layer-registry";
import { LAYER_WINDOW_PRESETS, type LayerWindowPreset } from "@/lib/regional-analysis-selection";
import { cn } from "@/lib/utils";
import { useLayerWindow, useLayerWindowStore } from "@/stores/layer-window-store";

/** "Sep 28" for a YYYY-MM-DD day, read in UTC so the label never shifts a day. */
function shortDay(day: string): string {
  return new Date(`${day}T00:00:00Z`).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}

/**
 * One dated layer's history window: a chip ("30d · to Sep 28") opening a four-preset menu
 * (menu button pattern). Overrides the global window for this layer only; see
 * stores/AGENTS.md §layer-window.
 */
export function LayerWindowChip({ layerId, label }: { layerId: LayerToggleId; label: string }) {
  const { preset, window } = useLayerWindow(layerId);
  const setLayerWindowPreset = useLayerWindowStore((state) => state.setLayerWindowPreset);
  const [isOpen, setIsOpen] = useState(false);
  const menuId = useId();
  const containerRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const itemRefs = useRef<(HTMLButtonElement | null)[]>([]);

  // Opening focuses the checked preset; an outside press closes without stealing focus.
  useEffect(() => {
    if (!isOpen) return;
    itemRefs.current[LAYER_WINDOW_PRESETS.indexOf(preset)]?.focus();
    const closeOnOutsidePress = (event: PointerEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) setIsOpen(false);
    };
    document.addEventListener("pointerdown", closeOnOutsidePress);
    return () => document.removeEventListener("pointerdown", closeOnOutsidePress);
    // `preset` is read only at open time; re-running on a pick would refocus a closing menu.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen]);

  if (window === null) return null;

  const close = () => {
    setIsOpen(false);
    triggerRef.current?.focus();
  };

  const choose = (next: LayerWindowPreset) => {
    setLayerWindowPreset(layerId, next);
    close();
  };

  const handleTriggerKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      setIsOpen(true);
    }
  };

  const handleMenuKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const items = itemRefs.current.filter((item): item is HTMLButtonElement => item !== null);
    const index = items.indexOf(document.activeElement as HTMLButtonElement);
    const focusAt = (next: number) => items[(next + items.length) % items.length]?.focus();
    if (event.key === "ArrowDown") focusAt(index + 1);
    else if (event.key === "ArrowUp") focusAt(index - 1);
    else if (event.key === "Home") focusAt(0);
    else if (event.key === "End") focusAt(items.length - 1);
    else if (event.key === "Escape") close();
    else if (event.key === "Tab") setIsOpen(false);
    else return;
    if (event.key !== "Tab") event.preventDefault();
  };

  const endLabel = shortDay(window.rangeEnd);

  return (
    <div ref={containerRef} className="relative">
      <button
        ref={triggerRef}
        type="button"
        aria-haspopup="menu"
        aria-expanded={isOpen}
        aria-controls={isOpen ? menuId : undefined}
        aria-label={`${label} history window: last ${preset} days to ${endLabel}. Change window`}
        data-testid={`layer-window-chip-${layerId}`}
        onClick={() => setIsOpen((open) => !open)}
        onKeyDown={handleTriggerKeyDown}
        className={cn(
          "inline-flex items-center gap-1 whitespace-nowrap rounded-(--radius) border px-1.5 py-0.5 text-[10px] tabular-nums max-sm:min-h-11 max-sm:px-3",
          "border-[hsl(var(--border))] text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--foreground))]",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--ring))]",
          isOpen && "text-[hsl(var(--foreground))]"
        )}
      >
        <History aria-hidden="true" className="h-3 w-3 shrink-0" />
        <span>
          {preset}d · to {endLabel}
        </span>
      </button>
      {isOpen && (
        <div
          id={menuId}
          role="menu"
          aria-label={`${label} history window`}
          onKeyDown={handleMenuKeyDown}
          className="absolute left-0 top-full z-20 mt-1 flex w-max flex-col rounded-(--radius) border border-[hsl(var(--border))] bg-[hsl(var(--popover))] text-[hsl(var(--popover-foreground))] p-0.5 shadow-lg"
        >
          {LAYER_WINDOW_PRESETS.map((option, index) => (
            <button
              key={option}
              ref={(element) => {
                itemRefs.current[index] = element;
              }}
              type="button"
              role="menuitemradio"
              aria-checked={option === preset}
              tabIndex={-1}
              onClick={() => choose(option)}
              className={cn(
                "rounded-(--radius) px-2 py-1 text-left text-[10px] tabular-nums max-sm:min-h-11 max-sm:text-xs",
                "hover:bg-[hsl(var(--muted))] focus-visible:bg-[hsl(var(--muted))] focus-visible:outline-none",
                option === preset
                  ? "font-medium text-[hsl(var(--foreground))]"
                  : "text-[hsl(var(--muted-foreground))]"
              )}
            >
              {option} days
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
