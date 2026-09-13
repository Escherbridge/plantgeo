import { canRenderRouteMeaning, ROUTE_MEANING_LABELS } from "./land-context-utils";
import type { RouteMeaning } from "./types";

interface RouteBadgeProps {
  routeMeaning: RouteMeaning;
  /** Must come from the specific office/route object, not asserted by the caller. */
  introductionCapability?: boolean;
}

const BADGE_STYLES: Record<RouteMeaning, string> = {
  records_assistance: "bg-sky-950/60 text-sky-300 border-sky-800",
  responsible_agency: "bg-emerald-950/60 text-emerald-300 border-emerald-800",
  advisory_sme: "bg-violet-950/60 text-violet-300 border-violet-800",
  contact_process_inquiry: "bg-amber-950/60 text-amber-300 border-amber-800",
  documented_introduction: "bg-rose-950/60 text-rose-300 border-rose-800",
};

/**
 * Renders one of the five fixed route-meaning labels. `documented_introduction` is structurally
 * gated: absent/false `introductionCapability` renders nothing, never a fallback claim, so a
 * caller cannot accidentally show a forwarding badge just by picking that enum value.
 */
export function RouteBadge({ routeMeaning, introductionCapability }: RouteBadgeProps) {
  if (!canRenderRouteMeaning(routeMeaning, introductionCapability)) {
    return null;
  }

  return (
    <span
      className={`inline-flex items-center rounded border px-2 py-0.5 text-xs font-medium ${BADGE_STYLES[routeMeaning]}`}
    >
      {ROUTE_MEANING_LABELS[routeMeaning]}
    </span>
  );
}
