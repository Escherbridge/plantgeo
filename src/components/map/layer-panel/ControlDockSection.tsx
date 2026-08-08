"use client";

import { useId, useRef, type ReactNode } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import {
  dockDisclosureRowClasses,
  useDockSectionScroll,
} from "@/components/map/layer-panel/dock-disclosure";
import { dockSectionDomId } from "@/components/map/layer-panel/dock-sections";
import { DockSectionIcon } from "@/components/map/layer-panel/layer-icons";
import { useDetailsExpanded, usePanelStore, type DockSectionId } from "@/stores/panel-store";

/**
 * The shell every CONTROL section shares: search, the map date, and render mode.
 *
 * A control section is not a `DetailsSection`, and the difference is the two-caret rule the
 * manager is built on. A report's caret MOUNTS a dynamically-imported region with its own
 * warehouse queries, which is why every report is shut by default. A control section's body is
 * a card that issues nothing, so collapsing it is a request for vertical space in a 19rem
 * column rather than a request to stop fetching -- and two of the three may therefore be seeded
 * open (`INITIALLY_EXPANDED_SECTIONS`) without weakening the mounting rule for reports.
 *
 * Extracted on 2026-08-09, when the floating search field and the bottom toolbar became
 * sections here. Three sections that open identically must not re-type the caret, the
 * `aria-controls` handshake and the pending-scroll consumption three times: that is how two
 * disclosures that merely look alike start behaving differently.
 */
export function ControlDockSection({
  id,
  label,
  children,
}: {
  id: DockSectionId;
  label: string;
  children: ReactNode;
}) {
  const isExpanded = useDetailsExpanded(id);
  const toggleDetails = usePanelStore((state) => state.toggleDetails);
  const containerRef = useRef<HTMLDivElement>(null);
  const bodyId = useId();

  useDockSectionScroll(id, containerRef);

  return (
    <div
      ref={containerRef}
      id={dockSectionDomId(id)}
      data-testid={`dock-section-${id}`}
      className="border-b border-(--glass-border) pb-1"
    >
      <button
        type="button"
        aria-expanded={isExpanded}
        // Named only while the body exists, the same rule every other disclosure here follows:
        // a collapsed section pointing at an absent region is a reference assistive technology
        // cannot resolve.
        aria-controls={isExpanded ? bodyId : undefined}
        onClick={() => toggleDetails(id)}
        className={dockDisclosureRowClasses(isExpanded)}
      >
        {isExpanded ? (
          <ChevronDown aria-hidden="true" className="h-3.5 w-3.5 shrink-0" />
        ) : (
          <ChevronRight aria-hidden="true" className="h-3.5 w-3.5 shrink-0" />
        )}
        <DockSectionIcon sectionKey={id} className="h-3.5 w-3.5 shrink-0 opacity-70" />
        <span className="min-w-0 flex-1 truncate">{label}</span>
      </button>

      {isExpanded && (
        // No `overflow-*` and no `max-h-*` on this wrapper, and none on anything inside it:
        // the manager has exactly one scroller (panel-scroll.ts rule 2), and a nested one is
        // precisely how the time card's single drag control becomes unreachable on a phone.
        <div id={bodyId} data-testid={`dock-section-body-${id}`} className="mb-1 mt-1">
          {children}
        </div>
      )}
    </div>
  );
}
