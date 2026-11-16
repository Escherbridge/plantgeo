"use client";

import { MapWebGLFallback } from "@/components/map/MapWebGLFallback";
import { useWebGLUnavailable } from "@/components/map/webgl-status";

/** Shown in the crawlable home overlay when the map canvas cannot start. */
export function HomeWebGLNotice() {
  const unavailable = useWebGLUnavailable();
  if (!unavailable) return null;
  return (
    <div className="px-page-inset pt-comfortable">
      <MapWebGLFallback fill={false} />
    </div>
  );
}
