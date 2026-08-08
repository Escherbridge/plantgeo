"use client";

import { ControlDockSection } from "@/components/map/layer-panel/ControlDockSection";
import TimeSlider from "@/components/map/TimeSlider";

/** The Time section's own title. Named for what it sets, which is the whole map's day. */
export const TIME_SECTION_LABEL = "Map date";

/**
 * The scrubber, as a section of the manager.
 *
 * It sits ABOVE the layer groups because the day governs all of them: every warehouse-backed
 * layer draws as of `selectedDate`, so a control ordered among the categories would read as
 * belonging to whichever one it landed next to. Until 2026-08-08 this card lived in a floating
 * top-right region behind a disclosure of its own; the region is gone and only the date pill
 * remains up there, which now opens this section rather than expanding in place. See
 * src/components/map/AGENTS.md.
 *
 * The caret, the `aria-controls` handshake and the scroll-on-request behaviour are
 * `ControlDockSection`'s -- see there for why a control section's caret is not a report's.
 * The card renders nothing at all until capabilities land, so a collapsed section and an
 * in-flight payload look the same, which is correct: neither has a day to state.
 */
export function TimeDockSection() {
  return (
    <ControlDockSection id="time" label={TIME_SECTION_LABEL}>
      <TimeSlider />
    </ControlDockSection>
  );
}
