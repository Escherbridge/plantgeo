import { useEffect } from "react";
import type { SoilFieldDepth, SoilFieldMeasure } from "@/lib/environmental/soil-field";
import type { LiveViewportRead } from "@/hooks/useViewportProxiedLayers";
import { useSoilStore } from "@/stores/soil-store";

/**
 * Records which depth a soil field's DRAWN answer is: the requested depth once a fresh
 * (non-retained) answer is live; a retained frame is still the previous depth's data and never
 * advances it. See src/components/map/AGENTS.md §soil-painted-depth.
 */
export function usePaintedSoilFieldDepth(
  measure: SoilFieldMeasure,
  read: Pick<LiveViewportRead<unknown>, "answer" | "isShowingRetainedAnswer">,
  requestedDepth: SoilFieldDepth
): void {
  const setPaintedFieldDepth = useSoilStore((state) => state.setPaintedFieldDepth);
  const answeredAtRequestedDepth = read.answer !== undefined && !read.isShowingRetainedAnswer;
  useEffect(() => {
    if (answeredAtRequestedDepth) setPaintedFieldDepth(measure, requestedDepth);
  }, [answeredAtRequestedDepth, measure, requestedDepth, setPaintedFieldDepth]);
}
