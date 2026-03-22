"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Navigation, ArrowUpDown, RotateCcw } from "lucide-react";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";
import { trpc } from "@/lib/trpc/client";
import { useRoutingStore } from "@/stores/routing-store";
import { WaypointInput } from "@/components/routing/WaypointInput";
import { ModeSelector } from "@/components/routing/ModeSelector";
import { DirectionsList } from "@/components/routing/DirectionsList";
import { RouteSummary } from "@/components/routing/RouteSummary";
import type { NormalizedGeocodingResult } from "@/lib/server/services/geocoding";

const COSTING_MAP = {
  car: "auto",
  bike: "bicycle",
  pedestrian: "pedestrian",
  truck: "truck",
} as const;

interface RoutingPanelProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function RoutingPanel({ open, onOpenChange }: RoutingPanelProps) {
  const router = useRouter();
  const searchParams = useSearchParams();

  const {
    origin,
    destination,
    transportMode,
    activeRoute,
    alternatives,
    isCalculating,
    setOrigin,
    setDestination,
    setActiveRoute,
    setAlternatives,
    setIsCalculating,
    setTransportMode,
    reset,
  } = useRoutingStore();

  const [originQuery, setOriginQuery] = useState("");
  const [destQuery, setDestQuery] = useState("");

  const routeMutation = trpc.routing.route.useMutation({
    onSuccess: (data) => {
      if (data.routes.length > 0) {
        setActiveRoute(data.routes[0]);
        setAlternatives(data.routes.slice(1));
      }
      setIsCalculating(false);
    },
    onError: () => {
      setIsCalculating(false);
    },
  });

  useEffect(() => {
    const oLat = searchParams.get("oLat");
    const oLon = searchParams.get("oLon");
    const oLabel = searchParams.get("oLabel");
    const dLat = searchParams.get("dLat");
    const dLon = searchParams.get("dLon");
    const dLabel = searchParams.get("dLabel");
    const mode = searchParams.get("mode");

    if (oLat && oLon) {
      const o = { lat: parseFloat(oLat), lon: parseFloat(oLon), label: oLabel ?? undefined };
      setOrigin(o);
      setOriginQuery(oLabel ?? `${oLat}, ${oLon}`);
    }
    if (dLat && dLon) {
      const d = { lat: parseFloat(dLat), lon: parseFloat(dLon), label: dLabel ?? undefined };
      setDestination(d);
      setDestQuery(dLabel ?? `${dLat}, ${dLon}`);
    }
    if (mode && ["car", "bike", "pedestrian", "truck"].includes(mode)) {
      setTransportMode(mode as "car" | "bike" | "pedestrian" | "truck");
    }
  }, []);

  const updateUrl = useCallback(
    (
      o: { lat: number; lon: number; label?: string } | null,
      d: { lat: number; lon: number; label?: string } | null,
      mode: string
    ) => {
      const params = new URLSearchParams(searchParams.toString());
      if (o) {
        params.set("oLat", String(o.lat));
        params.set("oLon", String(o.lon));
        if (o.label) params.set("oLabel", o.label);
      } else {
        params.delete("oLat");
        params.delete("oLon");
        params.delete("oLabel");
      }
      if (d) {
        params.set("dLat", String(d.lat));
        params.set("dLon", String(d.lon));
        if (d.label) params.set("dLabel", d.label);
      } else {
        params.delete("dLat");
        params.delete("dLon");
        params.delete("dLabel");
      }
      params.set("mode", mode);
      router.replace(`?${params.toString()}`, { scroll: false });
    },
    [router, searchParams]
  );

  function handleOriginSelect(result: NormalizedGeocodingResult) {
    const o = { lat: result.coordinates[1], lon: result.coordinates[0], label: result.name };
    setOrigin(o);
    setOriginQuery(result.name);
    updateUrl(o, destination, transportMode);
  }

  function handleDestSelect(result: NormalizedGeocodingResult) {
    const d = { lat: result.coordinates[1], lon: result.coordinates[0], label: result.name };
    setDestination(d);
    setDestQuery(result.name);
    updateUrl(origin, d, transportMode);
  }

  function handleSwap() {
    if (!origin || !destination) return;
    setOrigin(destination);
    setDestination(origin);
    setOriginQuery(destQuery);
    setDestQuery(originQuery);
    updateUrl(destination, origin, transportMode);
  }

  function handleCalculate() {
    if (!origin || !destination) return;
    setIsCalculating(true);
    routeMutation.mutate({
      locations: [
        { lat: origin.lat, lon: origin.lon },
        { lat: destination.lat, lon: destination.lon },
      ],
      costing: COSTING_MAP[transportMode],
      alternates: 2,
    });
  }

  function handleReset() {
    reset();
    setOriginQuery("");
    setDestQuery("");
    const params = new URLSearchParams(searchParams.toString());
    params.delete("oLat");
    params.delete("oLon");
    params.delete("oLabel");
    params.delete("dLat");
    params.delete("dLon");
    params.delete("dLabel");
    router.replace(`?${params.toString()}`, { scroll: false });
  }

  const canCalculate = !!origin && !!destination && !isCalculating;

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="left" onOpenChange={onOpenChange}>
        <SheetHeader>
          <SheetTitle className="flex items-center gap-2">
            <Navigation className="h-5 w-5" />
            Routing
          </SheetTitle>
        </SheetHeader>

        <div className="flex flex-col gap-3 mt-4">
          <ModeSelector />

          <div className="relative flex flex-col gap-2">
            <WaypointInput
              placeholder="Origin"
              value={originQuery}
              onChange={setOriginQuery}
              onSelect={handleOriginSelect}
              onClear={() => {
                setOrigin(null);
                setOriginQuery("");
                updateUrl(null, destination, transportMode);
              }}
            />

            <button
              type="button"
              onClick={handleSwap}
              disabled={!origin || !destination}
              className="absolute right-2 top-1/2 -translate-y-1/2 z-10 rounded-full bg-[hsl(var(--background))] border border-[hsl(var(--border))] p-1 text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--foreground))] disabled:opacity-40"
              title="Swap origin and destination"
            >
              <ArrowUpDown className="h-3.5 w-3.5" />
            </button>

            <WaypointInput
              placeholder="Destination"
              value={destQuery}
              onChange={setDestQuery}
              onSelect={handleDestSelect}
              onClear={() => {
                setDestination(null);
                setDestQuery("");
                updateUrl(origin, null, transportMode);
              }}
            />
          </div>

          <div className="flex gap-2">
            <Button
              className="flex-1 bg-emerald-500 hover:bg-emerald-600 text-white"
              disabled={!canCalculate}
              onClick={handleCalculate}
            >
              {isCalculating ? "Calculating..." : "Get Directions"}
            </Button>
            <Button variant="outline" size="icon" onClick={handleReset} title="Reset">
              <RotateCcw className="h-4 w-4" />
            </Button>
          </div>

          {routeMutation.isError && (
            <p className="text-sm text-[hsl(var(--destructive))]">
              Failed to calculate route. Please try again.
            </p>
          )}

          {activeRoute && (
            <>
              <RouteSummary
                route={activeRoute}
                alternatives={alternatives}
              />
              <DirectionsList maneuvers={activeRoute.maneuvers} />
            </>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}
