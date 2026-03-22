"use client";

import { useEffect, useRef } from "react";
import { MapboxOverlay } from "@deck.gl/mapbox";
import type { Layer } from "@deck.gl/core";
import type { IControl } from "maplibre-gl";
import { useMap } from "@/lib/map/map-context";

interface DeckOverlayProps {
  layers: Layer[];
}

export default function DeckOverlay({ layers }: DeckOverlayProps) {
  const map = useMap();
  const overlayRef = useRef<MapboxOverlay | null>(null);

  useEffect(() => {
    if (!map) return;

    const overlay = new MapboxOverlay({
      interleaved: true,
      layers,
    });

    overlayRef.current = overlay;
    map.addControl(overlay as unknown as IControl);

    return () => {
      map.removeControl(overlay as unknown as IControl);
      overlayRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map]);

  useEffect(() => {
    if (overlayRef.current) {
      overlayRef.current.setProps({ layers });
    }
  }, [layers]);

  return null;
}
