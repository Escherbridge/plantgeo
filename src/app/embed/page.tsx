"use client";

import dynamic from "next/dynamic";
import { Suspense } from "react";

const EmbedMap = dynamic(() => import("@/components/embed/EmbedMap"), {
  ssr: false,
  loading: () => (
    <div className="flex h-screen w-full items-center justify-center bg-black">
      <div className="text-white text-sm">Loading map...</div>
    </div>
  ),
});

export default function EmbedPage() {
  return (
    <Suspense
      fallback={
        <div className="flex h-screen w-full items-center justify-center bg-black">
          <div className="text-white text-sm">Loading...</div>
        </div>
      }
    >
      <EmbedMap />
    </Suspense>
  );
}
