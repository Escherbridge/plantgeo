"use client";

import { TripsLayer as DeckTripsLayer } from "@deck.gl/geo-layers";
import type { Color } from "@deck.gl/core";
import { DECK_DEFAULT_PROPS } from "@/lib/map/deck-config";

export interface TripDatum {
  path: [number, number][];
  timestamps: number[];
  color?: Color;
}

interface TripsLayerProps {
  id?: string;
  data: TripDatum[];
  currentTime: number;
  trailLength?: number;
  fadeTrail?: boolean;
  widthMinPixels?: number;
  onHover?: (info: { object?: TripDatum; x: number; y: number }) => void;
  onClick?: (info: { object?: TripDatum }) => void;
}

export function createTripsLayer({
  id = "trips",
  data,
  currentTime,
  trailLength = 120,
  fadeTrail = true,
  widthMinPixels = 2,
  onHover,
  onClick,
}: TripsLayerProps) {
  return new DeckTripsLayer<TripDatum>({
    ...DECK_DEFAULT_PROPS,
    id,
    data,
    getPath: (d) => d.path,
    getTimestamps: (d) => d.timestamps,
    getColor: (d): Color => d.color ?? [253, 128, 93],
    currentTime,
    trailLength,
    fadeTrail,
    widthMinPixels,
    onHover,
    onClick,
  });
}
