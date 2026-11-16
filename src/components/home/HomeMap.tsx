"use client";

import dynamic from "next/dynamic";
import { useState } from "react";
import MapLayout from "@/components/layout/MapLayout";
import { MapErrorBoundary } from "@/components/map/MapErrorBoundary";
import { useWebGLUnavailable } from "@/components/map/webgl-status";

const MapView = dynamic(() => import("@/components/map/MapView"), {
  ssr: false,
  loading: () => (
    <div className="flex h-full w-full items-center justify-center bg-[hsl(var(--background))]">
      <div className="text-[hsl(var(--foreground))]">Loading map...</div>
    </div>
  ),
});

export function HomeMap() {
  const [ready, setReady] = useState(false);
  const blocked = useWebGLUnavailable();
  const showMap = ready && !blocked;

  return (
    <MapLayout>
      <div
        className="h-full w-full"
        {...(showMap ? { "data-map-ready": "" } : {})}
        aria-hidden={blocked ? true : undefined}
      >
        <MapErrorBoundary onError={() => setReady(false)}>
          <MapView onReady={() => setReady(true)} onUnavailable={() => setReady(false)} />
        </MapErrorBoundary>
      </div>
    </MapLayout>
  );
}
