"use client";

import { useEffect, useState } from "react";
import { climateFieldSignalName } from "@/lib/environmental/climate-field";
import { climateFieldSignalForGeometryLayerId } from "@/lib/map/climate-field-layer-ids";
import type { LayerToggleId } from "@/lib/map/layer-registry";
import {
  describeLaneDistribution,
  distributionSignalName,
  selectDistributionLane,
  windowedToggleForStyleLayer,
} from "@/lib/layer-window-distribution";
import { trpc } from "@/lib/trpc/client";
import { useClimateStore } from "@/stores/climate-store";
import { useLayerWindow } from "@/stores/layer-window-store";
import { useSoilStore } from "@/stores/soil-store";
import { hasSelectableDay, useTimeSliderStore } from "@/stores/time-slider-store";

const DISTRIBUTION_STALE_MS = 15 * 60 * 1000;

interface WindowDistributionLineProps {
  /** The MapLibre style layer the tooltip is describing. */
  styleLayerId: string;
  lngLat: { lng: number; lat: number } | null;
  /** How long the point must hold still before asking; 0 for a tap, which already settled. */
  settleMs: number;
}

function roundToFourPlaces(value: number): number {
  return Math.round(value * 10_000) / 10_000;
}

/** One line under the tooltip's value: the layer's distribution over its own window. */
export function WindowDistributionLine({ styleLayerId, lngLat, settleMs }: WindowDistributionLineProps) {
  const layerId = windowedToggleForStyleLayer(styleLayerId);
  const isDated = useTimeSliderStore(
    (state) => layerId !== null && hasSelectableDay(state.capabilities, layerId)
  );
  if (layerId === null || !isDated || lngLat === null) return null;
  return (
    <DistributionQueryLine
      layerId={layerId}
      styleLayerId={styleLayerId}
      longitude={roundToFourPlaces(lngLat.lng)}
      latitude={roundToFourPlaces(lngLat.lat)}
      settleMs={settleMs}
    />
  );
}

function DistributionQueryLine({
  layerId,
  styleLayerId,
  longitude,
  latitude,
  settleMs,
}: {
  layerId: LayerToggleId;
  styleLayerId: string;
  longitude: number;
  latitude: number;
  settleMs: number;
}) {
  const { window } = useLayerWindow(layerId);
  const airTemperatureVariant = useClimateStore((state) => state.airTemperatureVariant);
  const soilFieldDepths = useSoilStore((state) => state.fieldDepth);
  const climateSignal = climateFieldSignalForGeometryLayerId(styleLayerId);
  // Asks agri for the painted signal only, so a multi-lane surface (air temperature's three
  // statistics, weather's four measures, a soil field's 3-4 depths) costs one lane read.
  const signalName = distributionSignalName(styleLayerId, airTemperatureVariant, soilFieldDepths);
  // The signal to DISPLAY, which is not always the one sent: climate's fixed-signal surfaces
  // (precipitation, soil-wetness-*) send no `signalName` because there is only one lane, but that
  // lane's own name must still be preferred so `selectDistributionLane` cannot fall through to
  // `lanes[0]` of a differently-ordered response. Every other surface's preferred signal is
  // exactly what was asked for.
  const preferredSignalName =
    climateSignal === null ? signalName : climateFieldSignalName(climateSignal, airTemperatureVariant);

  // Asks only once the point holds still, so sweeping a pointer across cells costs nothing. The
  // point is the FIRST position on the hovered feature (HoverTooltip keeps it), so moving within
  // one cell leaves this key -- and the line -- alone.
  const requestKey =
    window === null ? null : `${longitude}|${latitude}|${window.rangeStart}|${window.rangeEnd}|${signalName ?? ""}`;
  const [settledKey, setSettledKey] = useState<string | null>(null);
  useEffect(() => {
    const timer = setTimeout(() => setSettledKey(requestKey), settleMs);
    return () => clearTimeout(timer);
  }, [requestKey, settleMs]);

  const query = trpc.layerWindow.distributionAtPoint.useQuery(
    {
      layerId,
      longitude,
      latitude,
      rangeStart: window?.rangeStart ?? "",
      rangeEnd: window?.rangeEnd ?? "",
      ...(signalName === null ? {} : { signalName }),
    },
    {
      enabled: requestKey !== null && settledKey === requestKey,
      staleTime: DISTRIBUTION_STALE_MS,
      retry: false,
      refetchOnWindowFocus: false,
    }
  );

  if (!query.data) return null;
  const lane = selectDistributionLane(query.data, preferredSignalName);
  const line = lane === null ? null : describeLaneDistribution(lane);
  if (line === null) return null;
  return (
    <p data-testid="layer-window-distribution" className="mt-1 tabular-nums text-[hsl(var(--muted-foreground))]">
      {line}
    </p>
  );
}
