"use client";

import { useId, useRef, type ReactNode } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import {
  dockDisclosureRowClasses,
  useDockSectionScroll,
} from "@/components/map/layer-panel/dock-disclosure";
import { DockDetailsBody } from "@/components/map/layer-panel/DockDetails";
import { dockSectionDomId } from "@/components/map/layer-panel/dock-sections";
import {
  useDetailsExpanded,
  usePanelStore,
  type DockDetailsId,
} from "@/stores/panel-store";

interface DetailsSectionProps {
  id: DockDetailsId;
  label: string;
  /** Drawn before the label on a section that has no layer rows to identify it. */
  icon?: ReactNode;
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
 * outside: the Ctrl/Cmd+K shortcut opens the dock at Search and the top-bar date pill opens it
 * at a layer, and `pendingScrollSection` is how either request reaches the one scroller.
 *
 * The row style and that handshake are imported from `dock-disclosure.ts`, which
 * `TimeDockSection` reads too -- the Time section is not a report and mounts no
 * `DockDetailsBody`, so it cannot be an instance of this component, but the dock's carets must
 * still be one control vocabulary rather than two that merely look alike.
 */
export function DetailsSection({ id, label, icon }: DetailsSectionProps) {
  const isExpanded = useDetailsExpanded(id);
  const toggleDetails = usePanelStore((state) => state.toggleDetails);
  const containerRef = useRef<HTMLDivElement>(null);
  const bodyId = useId();

  useDockSectionScroll(id, containerRef);

  return (
    <div ref={containerRef} id={dockSectionDomId(id)} data-testid={`dock-section-${id}`}>
      <button
        type="button"
        aria-expanded={isExpanded}
        // Only names the body region while it exists: the body mounts (see below) only while
        // expanded, so pointing at it while collapsed would be a dangling reference.
        aria-controls={isExpanded ? bodyId : undefined}
        onClick={() => toggleDetails(id)}
        className={dockDisclosureRowClasses(isExpanded)}
      >
        {isExpanded ? (
          <ChevronDown aria-hidden="true" className="h-3.5 w-3.5 shrink-0" />
        ) : (
          <ChevronRight aria-hidden="true" className="h-3.5 w-3.5 shrink-0" />
        )}
        {icon}
        <span className="min-w-0 flex-1 truncate">{label}</span>
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
