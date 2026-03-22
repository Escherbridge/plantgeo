"use client";

import { useState, useMemo } from "react";
import { MapPin, BarChart3, X } from "lucide-react";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { trpc } from "@/lib/trpc/client";
import { StrategyCard } from "@/components/panels/StrategyCard";
import type { StrategyScore } from "@/lib/server/services/strategy-scoring";
import type { Supplier } from "@/lib/server/services/plantcommerce-api";

// ── Radar chart ───────────────────────────────────────────────────────────────

const RADAR_DIMENSIONS = [
  { key: "waterStress", label: "Water Stress" },
  { key: "soilHealth", label: "Soil Health" },
  { key: "fireRisk", label: "Fire Risk" },
  { key: "vegetationDegradation", label: "Veg. Degradation" },
  { key: "communityDemand", label: "Community" },
] as const;

type RadarKey = (typeof RADAR_DIMENSIONS)[number]["key"];

const RADAR_COLORS = ["#2196f3", "#4caf50", "#ff9800"];

interface RadarChartProps {
  strategies: StrategyScore[];
}

function RadarChart({ strategies }: RadarChartProps) {
  const size = 220;
  const cx = size / 2;
  const cy = size / 2;
  const maxR = 80;
  const n = RADAR_DIMENSIONS.length;

  function polarToCartesian(angle: number, r: number) {
    // Start at top (−π/2), go clockwise
    const rad = (angle * Math.PI) / 180 - Math.PI / 2;
    return {
      x: cx + r * Math.cos(rad),
      y: cy + r * Math.sin(rad),
    };
  }

  const angleStep = 360 / n;
  const axisPoints = RADAR_DIMENSIONS.map((_, i) =>
    polarToCartesian(i * angleStep, maxR)
  );

  // Grid rings at 25, 50, 75, 100
  const rings = [25, 50, 75, 100].map((pct) => {
    const r = (pct / 100) * maxR;
    const pts = RADAR_DIMENSIONS.map((_, i) => polarToCartesian(i * angleStep, r));
    return pts.map((p, i) => `${i === 0 ? "M" : "L"}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ") + " Z";
  });

  function strategyPolygon(strategy: StrategyScore, color: string) {
    const pts = RADAR_DIMENSIONS.map(({ key }, i) => {
      const value = strategy.factors[key as RadarKey] ?? 0;
      const r = (value / 100) * maxR;
      return polarToCartesian(i * angleStep, r);
    });
    const d = pts.map((p, i) => `${i === 0 ? "M" : "L"}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ") + " Z";
    return (
      <g key={strategy.strategyId}>
        <path d={d} fill={color} fillOpacity={0.15} stroke={color} strokeWidth={1.5} />
        {pts.map((p, i) => (
          <circle key={i} cx={p.x} cy={p.y} r={3} fill={color} />
        ))}
      </g>
    );
  }

  return (
    <div className="flex flex-col items-center gap-3">
      <svg width={size} height={size} className="overflow-visible">
        {/* Grid rings */}
        {rings.map((d, i) => (
          <path key={i} d={d} fill="none" stroke="hsl(var(--border))" strokeWidth={0.5} />
        ))}

        {/* Axis lines */}
        {axisPoints.map((pt, i) => (
          <line
            key={i}
            x1={cx}
            y1={cy}
            x2={pt.x}
            y2={pt.y}
            stroke="hsl(var(--border))"
            strokeWidth={0.5}
          />
        ))}

        {/* Axis labels */}
        {RADAR_DIMENSIONS.map(({ label }, i) => {
          const pt = polarToCartesian(i * angleStep, maxR + 16);
          return (
            <text
              key={i}
              x={pt.x}
              y={pt.y}
              textAnchor="middle"
              dominantBaseline="middle"
              fontSize={8}
              fill="hsl(var(--muted-foreground))"
            >
              {label}
            </text>
          );
        })}

        {/* Strategy polygons */}
        {strategies.slice(0, 3).map((s, i) => strategyPolygon(s, RADAR_COLORS[i]))}
      </svg>

      {/* Legend */}
      <div className="flex flex-wrap gap-3 justify-center">
        {strategies.slice(0, 3).map((s, i) => (
          <div key={s.strategyId} className="flex items-center gap-1.5">
            <span
              className="w-3 h-3 rounded-sm shrink-0"
              style={{ backgroundColor: RADAR_COLORS[i] }}
            />
            <span className="text-[10px] text-[hsl(var(--muted-foreground))]">
              {s.name}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── StrategyPanel ─────────────────────────────────────────────────────────────

export interface StrategyPanelProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Clicked map location */
  lat?: number;
  lon?: number;
}

export function StrategyPanel({
  open,
  onOpenChange,
  lat,
  lon,
}: StrategyPanelProps) {
  const [compareIds, setCompareIds] = useState<string[]>([]);
  const [compareMode, setCompareMode] = useState(false);
  /** Map of strategyId → supplier array (loaded on demand) */
  const [supplierMap, setSupplierMap] = useState<Record<string, Supplier[]>>({});
  const [loadingSuppliers, setLoadingSuppliers] = useState<Record<string, boolean>>({});
  /** strategyId being queried for suppliers (drives the tRPC query) */
  const [supplierQuery, setSupplierQuery] = useState<{
    strategyId: string;
    lat: number;
    lon: number;
  } | null>(null);

  const hasLocation = lat !== undefined && lon !== undefined;

  // ── Recommendations query ──────────────────────────────────────────────────

  const recsQuery = trpc.strategy.getStrategyRecommendations.useQuery(
    { lat: lat ?? 0, lon: lon ?? 0 },
    { enabled: open && hasLocation, staleTime: 60_000 }
  );

  // ── Priority zones query ───────────────────────────────────────────────────
  // Fetch all priority zones once (no strategyType filter — we filter client-side)

  const zonesQuery = trpc.community.getPriorityZones.useQuery(
    {},
    { enabled: open, staleTime: 120_000 }
  );

  // Build a map of strategyType → totalVotes for quick badge lookup
  const communityDemandByStrategy = useMemo(() => {
    const map: Record<string, number> = {};
    for (const zone of zonesQuery.data ?? []) {
      const st = (zone as { strategyType?: string }).strategyType;
      const votes = (zone as { totalVotes?: number }).totalVotes ?? 0;
      if (st) {
        map[st] = (map[st] ?? 0) + votes;
      }
    }
    return map;
  }, [zonesQuery.data]);

  // ── Supplier tRPC query (driven by supplierQuery state) ───────────────────

  const suppliersResult = trpc.strategy.getStrategySuppliers.useQuery(
    supplierQuery ?? { strategyId: "", lat: 0, lon: 0 },
    {
      enabled: supplierQuery !== null,
      staleTime: 3_600_000,
    }
  );

  // When suppliersResult resolves, push into supplierMap
  useMemo(() => {
    if (suppliersResult.data && supplierQuery) {
      setSupplierMap((prev) => ({
        ...prev,
        [supplierQuery.strategyId]: suppliersResult.data,
      }));
      setLoadingSuppliers((prev) => ({ ...prev, [supplierQuery.strategyId]: false }));
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [suppliersResult.data]);

  // ── Handlers ──────────────────────────────────────────────────────────────

  function handleCompareToggle(strategyId: string) {
    setCompareIds((prev) => {
      if (prev.includes(strategyId)) return prev.filter((id) => id !== strategyId);
      if (prev.length >= 3) return prev; // max 3
      return [...prev, strategyId];
    });
  }

  function handleViewSuppliers(strategyId: string) {
    if (!hasLocation || supplierMap[strategyId] !== undefined) return;
    setLoadingSuppliers((prev) => ({ ...prev, [strategyId]: true }));
    setSupplierQuery({ strategyId, lat: lat!, lon: lon! });
  }

  const strategies = recsQuery.data ?? [];

  const compareStrategies = useMemo(
    () => strategies.filter((s) => compareIds.includes(s.strategyId)),
    [strategies, compareIds]
  );

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="left" onOpenChange={onOpenChange}>
        <SheetHeader>
          <SheetTitle className="flex items-center gap-2">
            <MapPin className="h-5 w-5 text-green-600" />
            Strategy Recommendations
          </SheetTitle>
        </SheetHeader>

        <div className="flex flex-col gap-3 mt-4 overflow-y-auto max-h-[calc(100vh-8rem)]">
          {/* Location indicator */}
          {hasLocation ? (
            <p className="text-xs text-[hsl(var(--muted-foreground))]">
              Showing recommendations for{" "}
              <span className="font-medium text-[hsl(var(--foreground))]">
                {lat.toFixed(4)}, {lon.toFixed(4)}
              </span>
            </p>
          ) : (
            <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--muted))] p-4 text-center">
              <MapPin className="h-6 w-6 text-[hsl(var(--muted-foreground))] mx-auto mb-2" />
              <p className="text-xs text-[hsl(var(--muted-foreground))]">
                Click a location on the map to get strategy recommendations.
              </p>
            </div>
          )}

          {/* Loading state */}
          {recsQuery.isLoading && hasLocation && (
            <div className="flex flex-col gap-2">
              {Array.from({ length: 3 }).map((_, i) => (
                <div
                  key={i}
                  className="h-20 rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--muted))] animate-pulse"
                />
              ))}
            </div>
          )}

          {/* Error state */}
          {recsQuery.isError && (
            <p className="text-xs text-[hsl(var(--destructive))]">
              Failed to load recommendations. Check connectivity.
            </p>
          )}

          {/* Compare toolbar */}
          {strategies.length > 0 && (
            <div className="flex items-center justify-between">
              <p className="text-xs text-[hsl(var(--muted-foreground))]">
                {strategies.length} strategies ranked
              </p>
              {compareIds.length >= 2 && (
                <button
                  onClick={() => setCompareMode((v) => !v)}
                  className="flex items-center gap-1 text-xs text-[hsl(var(--primary))] hover:underline"
                >
                  <BarChart3 className="h-3.5 w-3.5" />
                  {compareMode ? "Hide radar" : `Compare ${compareIds.length}`}
                </button>
              )}
              {compareIds.length > 0 && (
                <button
                  onClick={() => {
                    setCompareIds([]);
                    setCompareMode(false);
                  }}
                  className="flex items-center gap-1 text-xs text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--foreground))]"
                >
                  <X className="h-3 w-3" />
                  Clear
                </button>
              )}
            </div>
          )}

          {/* Radar comparison chart */}
          {compareMode && compareStrategies.length >= 2 && (
            <div className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-4">
              <p className="text-xs font-semibold text-[hsl(var(--foreground))] mb-3">
                Strategy Comparison — Factor Radar
              </p>
              <RadarChart strategies={compareStrategies} />
            </div>
          )}

          {/* Strategy cards */}
          {strategies.map((strategy) => (
            <StrategyCard
              key={strategy.strategyId}
              strategy={strategy}
              suppliers={supplierMap[strategy.strategyId] ?? []}
              suppliersLoading={loadingSuppliers[strategy.strategyId] ?? false}
              communityDemandCount={communityDemandByStrategy[strategy.strategyId]}
              selected={compareIds.includes(strategy.strategyId)}
              onCompareToggle={handleCompareToggle}
              onViewSuppliers={handleViewSuppliers}
            />
          ))}

          {/* Empty state after load */}
          {!recsQuery.isLoading && !recsQuery.isError && strategies.length === 0 && hasLocation && (
            <p className="text-xs text-[hsl(var(--muted-foreground))]">
              No strategy recommendations found for this location.
            </p>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}
