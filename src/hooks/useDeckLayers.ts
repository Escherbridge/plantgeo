"use client";

import { useMemo } from "react";
import type { Layer } from "@deck.gl/core";
import { useMapStore } from "@/stores/map-store";

const LAYER_IDS = [
  "heatmap",
  "scatter",
  "geojson",
  "hex",
  "column",
  "arc",
  "trips",
  "path",
] as const;

type LayerId = (typeof LAYER_IDS)[number];

export function useDeckLayers(
  layerMap: Partial<Record<LayerId, Layer>>
): Layer[] {
  const activeLayers = useMapStore((state) => state.activeLayers);

  return useMemo(() => {
    return LAYER_IDS.filter((id) => activeLayers.includes(id))
      .map((id) => layerMap[id])
      .filter((layer): layer is Layer => layer !== undefined);
  }, [activeLayers, layerMap]);
}
