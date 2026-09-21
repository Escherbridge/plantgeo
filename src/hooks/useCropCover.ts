"use client";

import { useEffect } from "react";
import { trpc } from "@/lib/trpc/client";
import { useLandContextStore } from "@/stores/land-context-store";
import { useViewportBounds } from "@/hooks/useViewportProxiedLayers";
import { landContextRungForViewport } from "@/hooks/useLandContextViewport";
import { WORLD_EXTENT_ENVELOPE } from "@/lib/map/world-extent";

/** The selected annual publication, shared by the dock and renderer. */
export function useCropCover() {
  const enabled = useLandContextStore((state) => state.cropCoverEnabled);
  const releaseDay = useLandContextStore((state) => state.cropCoverReleaseDay);
  const setLatestDay = useLandContextStore((state) => state.setCropCoverLatestPublishedDay);
  const viewport = useViewportBounds();
  const availability = trpc.landContext.cropAvailability.useQuery(undefined, { staleTime: 60_000, refetchInterval: 60_000 });
  useEffect(() => {
    if (availability.data) setLatestDay(availability.data.latestDay);
  }, [availability.data, setLatestDay]);
  const parts = viewport.bbox?.split(",").map(Number);
  const bbox = parts?.length === 4 && parts.every(Number.isFinite) && parts[0] < parts[2] && parts[1] < parts[3]
    ? { west: parts[0], south: parts[1], east: parts[2], north: parts[3] } : null;
  const day = releaseDay ?? availability.data?.latestDay ?? null;
  const query = trpc.landContext.cropCoverInArea.useQuery({
    bbox: bbox ?? WORLD_EXTENT_ENVELOPE,
    asOfDay: day ?? "1970-01-01",
    zoomTier: landContextRungForViewport(viewport.zoom, 0) ?? 0,
  }, { enabled: enabled && availability.data?.available === true && !availability.isError && day !== null && bbox !== null,
    staleTime: 60 * 60 * 1000 });
  return { enabled, day, availability, query };
}
