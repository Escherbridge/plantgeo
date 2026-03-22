"use client";

import { useState, useEffect } from "react";
import { WidgetCard } from "./WidgetCard";
import { TimeSeriesChart } from "./TimeSeriesChart";
import { BarChart } from "./BarChart";
import { PieChart } from "./PieChart";
import { StatCard } from "./StatCard";
import { SpatialStats } from "./SpatialStats";
import { MetricsBar } from "./MetricsBar";

type PresetName = "Fire Monitoring" | "Fleet Overview" | "Environmental";

const PRESETS: Record<PresetName, string[]> = {
  "Fire Monitoring": ["metrics", "activeFires", "fireTimeline", "spatialStats"],
  "Fleet Overview": ["metrics", "fleetStatus", "routeActivity", "spatialStats"],
  Environmental: ["metrics", "sensorReadings", "landUse", "spatialStats"],
};

const STORAGE_KEY = "plantgeo_dashboard_layout";

interface SavedLayout {
  preset: PresetName;
  visible: string[];
}

function loadLayout(): SavedLayout | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    return JSON.parse(raw) as SavedLayout;
  } catch {
    return null;
  }
}

function saveLayout(layout: SavedLayout) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(layout));
  } catch {
    // ignore
  }
}

// Stable sample data for charts
const FIRE_TIMELINE = [
  { time: "Mon", value: 12 },
  { time: "Tue", value: 18 },
  { time: "Wed", value: 9 },
  { time: "Thu", value: 24 },
  { time: "Fri", value: 15 },
  { time: "Sat", value: 7 },
  { time: "Sun", value: 21 },
];

const FLEET_STATUS = [
  { label: "Active", value: 34 },
  { label: "Idle", value: 12 },
  { label: "Offline", value: 5 },
];

const ROUTE_ACTIVITY = [
  { time: "06:00", value: 8 },
  { time: "09:00", value: 22 },
  { time: "12:00", value: 18 },
  { time: "15:00", value: 27 },
  { time: "18:00", value: 14 },
  { time: "21:00", value: 5 },
];

const SENSOR_READINGS = [
  { time: "00:00", value: 42 },
  { time: "04:00", value: 38 },
  { time: "08:00", value: 51 },
  { time: "12:00", value: 67 },
  { time: "16:00", value: 73 },
  { time: "20:00", value: 58 },
];

const LAND_USE = [
  { label: "Forest", value: 45, color: "#10b981" },
  { label: "Urban", value: 20, color: "#6366f1" },
  { label: "Agriculture", value: 25, color: "#f59e0b" },
  { label: "Water", value: 10, color: "#3b82f6" },
];

const ACTIVE_FIRES = [
  { label: "Critical", value: 3 },
  { label: "High", value: 7 },
  { label: "Medium", value: 14 },
  { label: "Low", value: 22 },
];

export function DashboardGrid() {
  const [preset, setPreset] = useState<PresetName>("Fire Monitoring");
  const [visible, setVisible] = useState<string[]>(PRESETS["Fire Monitoring"]);

  useEffect(() => {
    const saved = loadLayout();
    if (saved) {
      setPreset(saved.preset);
      setVisible(saved.visible);
    }
  }, []);

  function handlePresetChange(next: PresetName) {
    const nextVisible = PRESETS[next];
    setPreset(next);
    setVisible(nextVisible);
    saveLayout({ preset: next, visible: nextVisible });
  }

  function hideWidget(id: string) {
    const next = visible.filter((v) => v !== id);
    setVisible(next);
    saveLayout({ preset, visible: next });
  }

  const show = (id: string) => visible.includes(id);

  return (
    <div className="flex flex-col gap-4">
      {/* Preset selector */}
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-xs text-[hsl(var(--muted-foreground))] font-medium uppercase tracking-wide">
          Preset:
        </span>
        {(Object.keys(PRESETS) as PresetName[]).map((p) => (
          <button
            key={p}
            onClick={() => handlePresetChange(p)}
            className={`px-3 py-1 rounded-full text-xs font-medium transition-colors border ${
              preset === p
                ? "bg-emerald-500 text-white border-emerald-500"
                : "border-[hsl(var(--border))] text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--foreground))]"
            }`}
          >
            {p}
          </button>
        ))}
      </div>

      {/* Metrics bar always visible */}
      {show("metrics") && (
        <WidgetCard title="Live Metrics" onClose={() => hideWidget("metrics")}>
          <MetricsBar />
        </WidgetCard>
      )}

      {/* Stat cards row */}
      <div className="grid grid-cols-2 gap-4 dashboard-stats-row">
        <StatCard
          label="Active Fires"
          value={46}
          trend="up"
          trendValue={12}
          sparkline={FIRE_TIMELINE.map((d) => d.value)}
          color="#ef4444"
        />
        <StatCard
          label="Fleet Online"
          value={34}
          trend="down"
          trendValue={3}
          sparkline={ROUTE_ACTIVITY.map((d) => d.value)}
          color="#10b981"
        />
      </div>

      {/* Widget grid */}
      <div className="grid grid-cols-1 gap-4 dashboard-widget-grid">
        {show("activeFires") && preset === "Fire Monitoring" && (
          <WidgetCard
            title="Active Fires by Severity"
            onClose={() => hideWidget("activeFires")}
            exportData={ACTIVE_FIRES}
          >
            <BarChart data={ACTIVE_FIRES} />
          </WidgetCard>
        )}

        {show("fireTimeline") && preset === "Fire Monitoring" && (
          <WidgetCard
            title="Fire Incidents This Week"
            onClose={() => hideWidget("fireTimeline")}
            exportData={FIRE_TIMELINE}
          >
            <TimeSeriesChart data={FIRE_TIMELINE} width={400} height={180} color="#ef4444" />
          </WidgetCard>
        )}

        {show("fleetStatus") && preset === "Fleet Overview" && (
          <WidgetCard
            title="Fleet Status Distribution"
            onClose={() => hideWidget("fleetStatus")}
            exportData={FLEET_STATUS}
          >
            <PieChart
              data={[
                { label: "Active", value: 34, color: "#10b981" },
                { label: "Idle", value: 12, color: "#f59e0b" },
                { label: "Offline", value: 5, color: "#ef4444" },
              ]}
            />
          </WidgetCard>
        )}

        {show("routeActivity") && preset === "Fleet Overview" && (
          <WidgetCard
            title="Route Activity (24h)"
            onClose={() => hideWidget("routeActivity")}
            exportData={ROUTE_ACTIVITY}
          >
            <TimeSeriesChart data={ROUTE_ACTIVITY} width={400} height={180} color="#6366f1" />
          </WidgetCard>
        )}

        {show("sensorReadings") && preset === "Environmental" && (
          <WidgetCard
            title="Sensor Readings (24h)"
            onClose={() => hideWidget("sensorReadings")}
            exportData={SENSOR_READINGS}
          >
            <TimeSeriesChart data={SENSOR_READINGS} width={400} height={180} color="#10b981" />
          </WidgetCard>
        )}

        {show("landUse") && preset === "Environmental" && (
          <WidgetCard
            title="Land Use Distribution"
            onClose={() => hideWidget("landUse")}
            exportData={LAND_USE}
          >
            <PieChart data={LAND_USE} />
          </WidgetCard>
        )}

        {show("spatialStats") && (
          <WidgetCard title="Spatial Statistics" onClose={() => hideWidget("spatialStats")}>
            <SpatialStats />
          </WidgetCard>
        )}
      </div>
    </div>
  );
}
