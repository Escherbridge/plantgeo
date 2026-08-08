"use client";

import { useId, useRef } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import {
  dockDisclosureRowClasses,
  useDockSectionScroll,
} from "@/components/map/layer-panel/dock-disclosure";
import { dockSectionDomId } from "@/components/map/layer-panel/dock-sections";
import { DockSectionIcon } from "@/components/map/layer-panel/layer-icons";
import TimeSlider from "@/components/map/TimeSlider";
import { useDetailsExpanded, usePanelStore } from "@/stores/panel-store";

/** The Time section's own title. Named for what it sets, which is the whole map's day. */
export const TIME_SECTION_LABEL = "Map date";

/**
 * The scrubber, as the first section of the dock.
 *
 * It sits ABOVE the layer groups because the day governs all of them: every warehouse-backed
 * layer draws as of `selectedDate`, so a control ordered among the categories would read as
 * belonging to whichever one it landed next to. Until 2026-08-08 this card lived in a floating
 * top-right region behind a disclosure of its own; the region is gone and only the date pill
 * remains up there, which now opens this section rather than expanding in place. See
 * src/components/map/AGENTS.md.
 *
 * Not a `DetailsSection`, and the difference is exactly the one that component's two-caret rule
 * draws. A report's caret MOUNTS a dynamically-imported region with its own warehouse queries,
 * which is why every report is shut by default. This body is the scrubber card: it subscribes
 * to `time-slider-store` and issues nothing, because the capabilities payload is fetched by the
 * always-mounted `TimeSliderCapabilitiesLoader` whether the dock is open or not. So it is open
 * by default (`INITIALLY_EXPANDED_SECTIONS`) without weakening the mounting rule for reports.
 *
 * The body is rendered unconditionally-mounted-when-expanded, matching the reports' shape, but
 * the reason is different again: collapsing it is a request for vertical space in a 19rem
 * column, not a request to stop fetching. The card renders nothing at all until capabilities
 * land, so a collapsed section and an in-flight payload look the same -- which is correct, since
 * neither has a day to state.
 */
export function TimeDockSection() {
  const isExpanded = useDetailsExpanded("time");
  const toggleDetails = usePanelStore((state) => state.toggleDetails);
  const containerRef = useRef<HTMLDivElement>(null);
  const bodyId = useId();

  useDockSectionScroll("time", containerRef);

  return (
    <div
      ref={containerRef}
      id={dockSectionDomId("time")}
      data-testid="dock-section-time"
      className="border-b border-(--glass-border) pb-1"
    >
      <button
        type="button"
        aria-expanded={isExpanded}
        // Named only while the body exists, the same rule every other disclosure here follows:
        // a collapsed section pointing at an absent region is a reference assistive technology
        // cannot resolve.
        aria-controls={isExpanded ? bodyId : undefined}
        onClick={() => toggleDetails("time")}
        className={dockDisclosureRowClasses(isExpanded)}
      >
        {isExpanded ? (
          <ChevronDown aria-hidden="true" className="h-3.5 w-3.5 shrink-0" />
        ) : (
          <ChevronRight aria-hidden="true" className="h-3.5 w-3.5 shrink-0" />
        )}
        <DockSectionIcon sectionKey="time" className="h-3.5 w-3.5 shrink-0 opacity-70" />
        <span className="min-w-0 flex-1 truncate">{TIME_SECTION_LABEL}</span>
      </button>

      {isExpanded && (
        // No `overflow-*` and no `max-h-*` on this wrapper, and none on the card inside it:
        // the dock has exactly one scroller (panel-scroll.ts rule 2), and a nested one is
        // precisely how the card's single drag control becomes unreachable on a phone.
        <div id={bodyId} data-testid="dock-section-body-time" className="mb-1 mt-1">
          <TimeSlider />
        </div>
      )}
    </div>
  );
}
