"use client";
// npm install recharts @react-pdf/renderer

import { useState, useEffect, useRef, useCallback } from "react";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import {
  Flame,
  Droplets,
  Wind,
  Leaf,
  BarChart3,
  TrendingUp,
  MapPin,
  Download,
  FileText,
} from "lucide-react";
import { trpc } from "@/lib/trpc/client";
import { TrendChart } from "@/components/charts/TrendChart";
import { RiskSummaryWidget } from "@/components/charts/RiskSummaryWidget";
import { PriorityTable } from "@/components/charts/PriorityTable";
import { exportCSV, exportPDF } from "@/lib/export/analytics-export";

// ─── Types ────────────────────────────────────────────────────────────────────

type MetricKey = "fire" | "drought" | "ndvi" | "water";
type DayRange = 7 | 30 | 90;
type Tab = "overview" | "trends" | "demand";

const STRATEGY_TYPES = [
  { key: "all", label: "All" },
  { key: "keyline", label: "Keyline" },
  { key: "silvopasture", label: "Silvopasture" },
  { key: "reforestation", label: "Reforestation" },
  { key: "biochar", label: "Biochar" },
  { key: "water_harvesting", label: "Water Harvesting" },
  { key: "cover_cropping", label: "Cover Cropping" },
];

const METRIC_OPTIONS: { key: MetricKey; label: string; color: string; unit: string }[] = [
  { key: "fire", label: "Fire Detections", color: "#ef4444", unit: "detections" },
  { key: "drought", label: "Drought Index", color: "#f97316", unit: "D-class" },
  { key: "ndvi", label: "NDVI Anomaly", color: "#22c55e", unit: "δNDVI" },
  { key: "water", label: "Streamflow %ile", color: "#3b82f6", unit: "%" },
];

// ─── Props ────────────────────────────────────────────────────────────────────

interface AnalyticsDashboardProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Current map viewport bbox: "west,south,east,north" */
  bbox?: string;
  /** Called when user clicks fly-to on a priority row */
  onFlyTo?: (lat: number, lon: number) => void;
  /** Called when demand heatmap toggle changes */
  onDemandHeatmapToggle?: (enabled: boolean) => void;
}

// ─── Drought label helper ────────────────────────────────────────────────────

function droughtLabel(cls: number): string {
  const labels = ["None", "D0", "D1", "D2", "D3", "D4"];
  return labels[Math.min(cls, 5)] ?? "D4";
}

// ─── Demand bar chart (pure SVG) ─────────────────────────────────────────────

interface StrategyCount {
  strategyType: string;
  count: number;
}

function DemandBarChart({ data }: { data: StrategyCount[] }) {
  if (!data.length) {
    return (
      <div className="text-xs text-[hsl(var(--muted-foreground))] py-4 text-center">
        No community requests in current viewport.
      </div>
    );
  }
  const max = Math.max(...data.map((d) => d.count), 1);
  return (
    <div className="flex flex-col gap-2">
      {data.map((d, i) => (
        <div key={i} className="flex items-center gap-2">
          <span className="text-xs text-[hsl(var(--muted-foreground))] w-28 truncate shrink-0">
            {d.strategyType}
          </span>
          <div className="flex-1 h-5 bg-[hsl(var(--muted))] rounded overflow-hidden">
            <div
              className="h-full bg-emerald-500 rounded transition-all"
              style={{ width: `${(d.count / max) * 100}%` }}
            />
          </div>
          <span className="text-xs font-medium tabular-nums w-6 text-right">{d.count}</span>
        </div>
      ))}
    </div>
  );
}

// ─── Main Component ───────────────────────────────────────────────────────────

export function AnalyticsDashboard({
  open,
  onOpenChange,
  bbox = "-180,-90,180,90",
  onFlyTo,
  onDemandHeatmapToggle,
}: AnalyticsDashboardProps) {
  const [tab, setTab] = useState<Tab>("overview");
  const [metric, setMetric] = useState<MetricKey>("fire");
  const [dayRange, setDayRange] = useState<DayRange>(30);
  const [strategyFilter, setStrategyFilter] = useState("all");
  const [demandHeatmap, setDemandHeatmap] = useState(false);
  const [debouncedBbox, setDebouncedBbox] = useState(bbox);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Debounce viewport changes by 500ms
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      setDebouncedBbox(bbox);
    }, 500);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [bbox]);

  // ── tRPC queries ────────────────────────────────────────────────────────────

  const riskQuery = trpc.analytics.getRegionalRiskSummary.useQuery(
    { bbox: debouncedBbox },
    { enabled: open, refetchInterval: 300_000 }
  );

  const trendQuery = trpc.analytics.getTrendData.useQuery(
    { bbox: debouncedBbox, metric, days: dayRange },
    { enabled: open && tab === "trends" }
  );

  const priorityQuery = trpc.analytics.getPrioritySubregions.useQuery(
    { bbox: debouncedBbox },
    { enabled: open }
  );

  const demandQuery = trpc.analytics.getDemandDensity.useQuery(
    { bbox: debouncedBbox },
    { enabled: open && tab === "demand" }
  );

  const risk = riskQuery.data;
  const priorities = priorityQuery.data ?? [];
  const trendData = trendQuery.data ?? [];

  // Aggregate demand by strategy type
  const demandFeatures = demandQuery.data?.features ?? [];
  const strategyCounts: StrategyCount[] = (() => {
    const map = new Map<string, number>();
    for (const f of demandFeatures) {
      const st = (f.properties as { strategyType?: string })?.strategyType ?? "unknown";
      if (strategyFilter !== "all" && st !== strategyFilter) continue;
      map.set(st, (map.get(st) ?? 0) + 1);
    }
    return Array.from(map.entries())
      .map(([strategyType, count]) => ({ strategyType, count }))
      .sort((a, b) => b.count - a.count);
  })();

  // ── Handlers ─────────────────────────────────────────────────────────────────

  const handleDemandHeatmapToggle = useCallback(() => {
    const next = !demandHeatmap;
    setDemandHeatmap(next);
    onDemandHeatmapToggle?.(next);
  }, [demandHeatmap, onDemandHeatmapToggle]);

  const handleExportCSV = useCallback(() => {
    if (tab === "overview") {
      const rows = priorities.map((p, i) => ({
        rank: i + 1,
        name: p.name,
        lat: p.lat,
        lon: p.lon,
        score: p.score,
        primaryIssue: p.primaryIssue,
      }));
      exportCSV(rows, "priority-subregions.csv");
    } else if (tab === "trends") {
      exportCSV(trendData as unknown as Record<string, unknown>[], `trend-${metric}.csv`);
    } else {
      exportCSV(
        strategyCounts as unknown as Record<string, unknown>[],
        "demand-density.csv"
      );
    }
  }, [tab, priorities, trendData, metric, strategyCounts]);

  const handleExportPDF = useCallback(() => {
    if (!risk) return;
    exportPDF(risk, trendData, priorities);
  }, [risk, trendData, priorities]);

  const metricConfig = METRIC_OPTIONS.find((m) => m.key === metric) ?? METRIC_OPTIONS[0];

  // ── Risk trend → widget trend direction ──────────────────────────────────────

  const riskTrendDir =
    risk?.riskTrend === "worsening" ? "up" : risk?.riskTrend === "improving" ? "down" : "stable";

  // ─── Render ──────────────────────────────────────────────────────────────────

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-full sm:max-w-lg" onOpenChange={onOpenChange}>
        <SheetHeader>
          <SheetTitle className="flex items-center gap-2">
            <BarChart3 className="h-5 w-5 text-emerald-500" />
            Environmental Analytics
          </SheetTitle>
        </SheetHeader>

        {/* Tab bar */}
        <div className="flex gap-1 mt-3 border-b border-[hsl(var(--border))]">
          {(["overview", "trends", "demand"] as Tab[]).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`px-3 py-1.5 text-xs font-medium capitalize transition-colors border-b-2 -mb-px ${
                tab === t
                  ? "border-emerald-500 text-emerald-600 dark:text-emerald-400"
                  : "border-transparent text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--foreground))]"
              }`}
            >
              {t === "overview" ? "Overview" : t === "trends" ? "Trends" : "Demand"}
            </button>
          ))}
          <div className="flex-1" />
          <button
            onClick={handleExportCSV}
            className="px-2 py-1.5 text-xs text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--foreground))] transition-colors flex items-center gap-1"
            title="Export CSV"
          >
            <Download className="h-3.5 w-3.5" />
            CSV
          </button>
        </div>

        <div className="flex flex-col gap-4 mt-4 overflow-y-auto max-h-[calc(100vh-10rem)]">

          {/* ── OVERVIEW TAB ─────────────────────────────────────────────────── */}
          {tab === "overview" && (
            <>
              {riskQuery.isLoading && (
                <p className="text-xs text-[hsl(var(--muted-foreground))]">
                  Loading regional risk data…
                </p>
              )}

              {/* 2×2 risk widgets */}
              <div className="grid grid-cols-2 gap-2">
                <RiskSummaryWidget
                  label="Fire Risk"
                  value={risk ? `${risk.fireRiskAvg}%` : "—"}
                  trend={riskTrendDir}
                  color={
                    (risk?.fireRiskAvg ?? 0) >= 60
                      ? "red"
                      : (risk?.fireRiskAvg ?? 0) >= 30
                      ? "orange"
                      : "green"
                  }
                  icon={<Flame className="h-3.5 w-3.5" />}
                />
                <RiskSummaryWidget
                  label="Drought Class"
                  value={risk ? droughtLabel(risk.droughtClass) : "—"}
                  trend={
                    (risk?.droughtClass ?? 0) >= 3
                      ? "up"
                      : (risk?.droughtClass ?? 0) <= 1
                      ? "down"
                      : "stable"
                  }
                  color={
                    (risk?.droughtClass ?? 0) >= 3
                      ? "red"
                      : (risk?.droughtClass ?? 0) >= 2
                      ? "orange"
                      : "green"
                  }
                  icon={<Wind className="h-3.5 w-3.5" />}
                />
                <RiskSummaryWidget
                  label="Streamflow %ile"
                  value={risk ? `${risk.streamflowPercentile}%` : "—"}
                  trend={
                    (risk?.streamflowPercentile ?? 50) <= 20
                      ? "up"
                      : (risk?.streamflowPercentile ?? 50) >= 70
                      ? "down"
                      : "stable"
                  }
                  color={
                    (risk?.streamflowPercentile ?? 50) <= 20
                      ? "red"
                      : (risk?.streamflowPercentile ?? 50) <= 40
                      ? "yellow"
                      : "blue"
                  }
                  icon={<Droplets className="h-3.5 w-3.5" />}
                />
                <RiskSummaryWidget
                  label="Active Fires"
                  value={risk ? risk.activeFireCount : "—"}
                  trend={
                    (risk?.activeFireCount ?? 0) > 20
                      ? "up"
                      : (risk?.activeFireCount ?? 0) === 0
                      ? "down"
                      : "stable"
                  }
                  color={
                    (risk?.activeFireCount ?? 0) > 20
                      ? "red"
                      : (risk?.activeFireCount ?? 0) > 5
                      ? "orange"
                      : "green"
                  }
                  icon={<Leaf className="h-3.5 w-3.5" />}
                />
              </div>

              {/* Priority table */}
              <div className="flex flex-col gap-2">
                <h3 className="text-xs font-semibold text-[hsl(var(--foreground))] flex items-center gap-1.5">
                  <TrendingUp className="h-3.5 w-3.5 text-orange-500" />
                  Priority Subregions
                </h3>
                {priorityQuery.isLoading ? (
                  <p className="text-xs text-[hsl(var(--muted-foreground))]">Loading…</p>
                ) : (
                  <PriorityTable data={priorities} onFlyTo={onFlyTo} />
                )}
              </div>

              {/* Export PDF */}
              <button
                onClick={handleExportPDF}
                disabled={!risk}
                className="flex items-center gap-2 text-xs px-3 py-2 rounded-lg border border-[hsl(var(--border))] hover:bg-[hsl(var(--accent))] transition-colors disabled:opacity-50"
              >
                <FileText className="h-4 w-4" />
                Export PDF Report
              </button>
            </>
          )}

          {/* ── TRENDS TAB ───────────────────────────────────────────────────── */}
          {tab === "trends" && (
            <>
              {/* Metric selector */}
              <div className="flex flex-wrap gap-1">
                {METRIC_OPTIONS.map((m) => (
                  <button
                    key={m.key}
                    onClick={() => setMetric(m.key)}
                    className={`px-2.5 py-1 rounded-full text-xs font-medium transition-colors ${
                      metric === m.key
                        ? "text-white"
                        : "bg-[hsl(var(--muted))] text-[hsl(var(--muted-foreground))] hover:bg-[hsl(var(--accent))]"
                    }`}
                    style={metric === m.key ? { backgroundColor: m.color } : {}}
                  >
                    {m.label}
                  </button>
                ))}
              </div>

              {/* Date range picker */}
              <div className="flex gap-1">
                {([7, 30, 90] as DayRange[]).map((d) => (
                  <button
                    key={d}
                    onClick={() => setDayRange(d)}
                    className={`px-2.5 py-1 rounded text-xs font-medium transition-colors ${
                      dayRange === d
                        ? "bg-emerald-500 text-white"
                        : "bg-[hsl(var(--muted))] text-[hsl(var(--muted-foreground))] hover:bg-[hsl(var(--accent))]"
                    }`}
                  >
                    {d}d
                  </button>
                ))}
              </div>

              {trendQuery.isLoading ? (
                <p className="text-xs text-[hsl(var(--muted-foreground))]">Loading trend data…</p>
              ) : (
                <TrendChart
                  data={trendData}
                  title={`${metricConfig.label} — last ${dayRange} days`}
                  color={metricConfig.color}
                  unit={metricConfig.unit}
                />
              )}
            </>
          )}

          {/* ── DEMAND TAB ───────────────────────────────────────────────────── */}
          {tab === "demand" && (
            <>
              {/* Strategy filter chips */}
              <div className="flex flex-wrap gap-1">
                {STRATEGY_TYPES.map((s) => (
                  <button
                    key={s.key}
                    onClick={() => setStrategyFilter(s.key)}
                    className={`px-2.5 py-1 rounded-full text-xs font-medium transition-colors ${
                      strategyFilter === s.key
                        ? "bg-emerald-500 text-white"
                        : "bg-[hsl(var(--muted))] text-[hsl(var(--muted-foreground))] hover:bg-[hsl(var(--accent))]"
                    }`}
                  >
                    {s.label}
                  </button>
                ))}
              </div>

              {/* Demand bar chart */}
              <div className="flex flex-col gap-2">
                <h3 className="text-xs font-semibold text-[hsl(var(--foreground))]">
                  Requests by Strategy Type
                </h3>
                {demandQuery.isLoading ? (
                  <p className="text-xs text-[hsl(var(--muted-foreground))]">Loading…</p>
                ) : (
                  <DemandBarChart data={strategyCounts} />
                )}
              </div>

              {/* Heatmap toggle */}
              <button
                onClick={handleDemandHeatmapToggle}
                className={`flex items-center gap-2 text-xs px-3 py-2 rounded-lg border transition-colors ${
                  demandHeatmap
                    ? "border-emerald-500 bg-emerald-50 dark:bg-emerald-950/20 text-emerald-700 dark:text-emerald-300"
                    : "border-[hsl(var(--border))] hover:bg-[hsl(var(--accent))]"
                }`}
              >
                <MapPin className="h-4 w-4" />
                {demandHeatmap ? "Hide" : "Show"} Demand Heatmap Layer
              </button>
            </>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}
