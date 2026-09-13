"use client";

import { useEffect, useRef } from "react";
import { LAND_CONTEXT_GROUP_LABELS, useLandContextStore } from "@/stores/land-context-store";

/**
 * The concise, dismissible hover/keyboard-focus identity card. Per spec: "Hover or keyboard
 * focus presents concise feature identity, category, source vintage and contact-route
 * summary ... A hover surface is dismissible and remains available while the user moves into
 * it." This is deliberately NOT a modal: no focus trap, no backdrop, and it never blocks the
 * pinned detail panel or map interaction underneath it.
 *
 * Positioned at the stored hover screen coordinates when available (pointer hover); falls back
 * to `undefined` positioning (i.e. CSS-anchored near the focused control) for keyboard focus
 * that did not originate from a pointer event, per the "hover OR keyboard focus" requirement.
 */
export function LandContextIdentityCard() {
  const hoveredFeature = useLandContextStore((state) => state.hoveredFeature);
  const hoverPosition = useLandContextStore((state) => state.hoverPosition);
  const setHoveredFeature = useLandContextStore((state) => state.setHoveredFeature);
  const cardRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!hoveredFeature) return;
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setHoveredFeature(null);
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [hoveredFeature, setHoveredFeature]);

  if (!hoveredFeature) return null;

  return (
    <div
      ref={cardRef}
      role="status"
      aria-live="polite"
      className="z-30 max-w-65 rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--background))]/95 p-3 pr-10 text-xs shadow-lg backdrop-blur-sm"
      style={
        hoverPosition
          ? { position: "absolute", left: hoverPosition.x, top: hoverPosition.y }
          : { position: "absolute" }
      }
      // Dismissible without losing pointer focus: staying over the card keeps it open (per
      // spec "remains available while the user moves into it"), only Escape/leaving both the
      // trigger and the card closes it.
      onMouseLeave={() => setHoveredFeature(null)}
    >
      <p className="font-semibold text-[hsl(var(--foreground))]">{hoveredFeature.title}</p>
      <p className="mt-1 text-[hsl(var(--muted-foreground))]">
        {LAND_CONTEXT_GROUP_LABELS[hoveredFeature.group]}
        {hoveredFeature.category ? ` · ${hoveredFeature.category}` : ""}
      </p>
      {hoveredFeature.sourceVintage ? (
        <p className="mt-1 text-[hsl(var(--muted-foreground))]">{hoveredFeature.sourceVintage}</p>
      ) : null}
      {hoveredFeature.contactRouteSummary ? (
        <p className="mt-1 text-[hsl(var(--muted-foreground))]">{hoveredFeature.contactRouteSummary}</p>
      ) : null}
      <button
        type="button"
        className="absolute right-0 top-0 flex h-11 w-11 min-h-11 min-w-11 items-center justify-center rounded text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--foreground))]"
        onClick={() => setHoveredFeature(null)}
      >
        Dismiss
      </button>
    </div>
  );
}
