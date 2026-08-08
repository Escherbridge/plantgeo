"use client";

import { useEffect, useId, useRef, type ReactNode } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { DockDetailsBody } from "@/components/map/layer-panel/DockDetails";
import { dockSectionDomId } from "@/components/map/layer-panel/dock-sections";
import {
  useDetailsExpanded,
  usePanelStore,
  type DockDetailsId,
} from "@/stores/panel-store";

/** True when the reader has asked the platform to stop animating things at them. */
function prefersReducedMotion(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return false;
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

interface DetailsSectionProps {
  id: DockDetailsId;
  label: string;
  /** Drawn before the label on a section that has no layer rows to identify it. */
  icon?: ReactNode;
  /** A live count or state chip, drawn after the label. Alerts is the only user today. */
  badge?: ReactNode;
}

/**
 * One collapsible report: a disclosure row, and the region it discloses.
 *
 * The distinction the two chevrons in this dock draw, and it is a real one: a GROUP header's
 * caret shows the layers in a category, which is free; this caret MOUNTS a report, which is a
 * fistful of warehouse queries. Keeping them as two controls is what lets the layer list stay
 * fully expanded by default while every report stays collapsed until asked for -- one control
 * over both would have had to pick, and either choice is wrong for half the dock.
 *
 * Expansion lives in `panel-store` rather than in local state because it is addressable from
 * outside: the toolbar's bell opens the dock at Alerts, and `pendingScrollSection` is how that
 * request reaches the one scroller.
 */
export function DetailsSection({ id, label, icon, badge }: DetailsSectionProps) {
  const isExpanded = useDetailsExpanded(id);
  const toggleDetails = usePanelStore((state) => state.toggleDetails);
  const pendingScrollSection = usePanelStore((state) => state.pendingScrollSection);
  const clearPendingScroll = usePanelStore((state) => state.clearPendingScroll);
  const containerRef = useRef<HTMLDivElement>(null);
  const bodyId = useId();

  useEffect(() => {
    if (pendingScrollSection !== id) return;
    const container = containerRef.current;
    // jsdom implements no scrollIntoView, and neither does an unmounted node; the request is
    // consumed either way so a stale target cannot pin a later expansion in place.
    if (container && typeof container.scrollIntoView === "function") {
      container.scrollIntoView({
        block: "start",
        behavior: prefersReducedMotion() ? "auto" : "smooth",
      });
    }
    clearPendingScroll();
  }, [pendingScrollSection, id, clearPendingScroll]);

  return (
    <div ref={containerRef} id={dockSectionDomId(id)} data-testid={`dock-section-${id}`}>
      <button
        type="button"
        aria-expanded={isExpanded}
        // Only names the body region while it exists: the body mounts (see below) only while
        // expanded, so pointing at it while collapsed would be a dangling reference.
        aria-controls={isExpanded ? bodyId : undefined}
        onClick={() => toggleDetails(id)}
        className={[
          "flex min-h-8 w-full items-center gap-1.5 rounded-md px-1 py-1 text-left text-[11px]",
          "font-medium transition-colors focus-visible:outline-none focus-visible:ring-2",
          "focus-visible:ring-[hsl(var(--ring))] max-sm:min-h-11",
          isExpanded
            ? "bg-[hsl(var(--muted)/0.5)] text-[hsl(var(--foreground))]"
            : "text-[hsl(var(--muted-foreground))] hover:bg-[hsl(var(--muted)/0.4)] hover:text-[hsl(var(--foreground))]",
        ].join(" ")}
      >
        {isExpanded ? (
          <ChevronDown aria-hidden="true" className="h-3.5 w-3.5 shrink-0" />
        ) : (
          <ChevronRight aria-hidden="true" className="h-3.5 w-3.5 shrink-0" />
        )}
        {icon}
        <span className="min-w-0 flex-1 truncate">{label}</span>
        {badge}
      </button>

      {isExpanded && (
        // The left rule is the whole visual claim: everything indented past it belongs to the
        // section above rather than to the dock, which is what keeps eight stacked reports
        // from reading as one long page.
        <div
          id={bodyId}
          data-testid={`dock-section-body-${id}`}
          className="mb-1 ml-1.75 mt-1 rounded-r-md border-l-2 border-l-[hsl(var(--primary)/0.6)] bg-[hsl(var(--muted)/0.25)] px-2 py-2"
        >
          <DockDetailsBody id={id} />
        </div>
      )}
    </div>
  );
}
