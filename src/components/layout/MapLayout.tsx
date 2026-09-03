"use client";

import { type ReactNode } from "react";

interface MapLayoutProps {
  children: ReactNode;
  sidePanel?: ReactNode;
  bottomSheet?: ReactNode;
  sidePanelOpen?: boolean;
  bottomSheetOpen?: boolean;
}

export default function MapLayout({
  children,
  sidePanel,
  bottomSheet,
  sidePanelOpen = true,
  bottomSheetOpen = false,
}: MapLayoutProps) {
  return (
    <div className="viewport-below-top-bar relative flex w-screen overflow-hidden bg-[hsl(var(--background))]">
      {sidePanel && sidePanelOpen && (
        <div className="relative z-20 h-full shrink-0">{sidePanel}</div>
      )}

      <div className="relative flex-1 overflow-hidden">{children}</div>

      {bottomSheet && bottomSheetOpen && (
        <div className="absolute inset-x-0 bottom-0 z-30">{bottomSheet}</div>
      )}
    </div>
  );
}
