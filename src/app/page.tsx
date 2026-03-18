"use client";

import dynamic from "next/dynamic";
import MapLayout from "@/components/layout/MapLayout";

const MapView = dynamic(() => import("@/components/map/MapView"), {
  ssr: false,
  loading: () => (
    <div className="flex h-screen w-full items-center justify-center bg-[hsl(var(--background))]">
      <div className="text-[hsl(var(--foreground))]">Loading map...</div>
    </div>
  ),
});

export default function HomePage() {
  return (
    <MapLayout>
      <MapView />
    </MapLayout>
  );
}
