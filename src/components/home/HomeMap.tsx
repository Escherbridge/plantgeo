"use client";

import dynamic from "next/dynamic";
import MapLayout from "@/components/layout/MapLayout";

const MapView = dynamic(
  () =>
    import("@/components/map/MapView").then((mod) => {
      function ReadyMapView() {
        return (
          <div data-map-ready="" className="h-full w-full">
            <mod.default />
          </div>
        );
      }
      return ReadyMapView;
    }),
  {
    ssr: false,
    loading: () => (
      <div className="flex h-full w-full items-center justify-center bg-[hsl(var(--background))]">
        <div className="text-[hsl(var(--foreground))]">Loading map...</div>
      </div>
    ),
  }
);

export function HomeMap() {
  return (
    <MapLayout>
      <MapView />
    </MapLayout>
  );
}
