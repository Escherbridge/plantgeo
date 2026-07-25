"use client";

import { useEffect, useState } from "react";
import { AlertTriangle } from "lucide-react";
import { WidgetCard } from "./WidgetCard";
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

function isPresetName(value: unknown): value is PresetName {
  return typeof value === "string" && value in PRESETS;
}

function loadLayout(): SavedLayout | null {
  try {
    const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "null") as Partial<SavedLayout> | null;
    if (!parsed || !isPresetName(parsed.preset) || !Array.isArray(parsed.visible)) return null;
    const allowed = new Set(PRESETS[parsed.preset]);
    return {
      preset: parsed.preset,
      visible: parsed.visible.filter(
        (item): item is string => typeof item === "string" && allowed.has(item)
      ),
    };
  } catch {
    return null;
  }
}

function saveLayout(layout: SavedLayout) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(layout));
  } catch {
    // Layout persistence is optional.
  }
}

function UnavailableEvidence({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex items-start gap-2 rounded-lg border border-dashed border-[hsl(var(--border))] p-4 text-sm text-[hsl(var(--muted-foreground))]">
      <AlertTriangle aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0 text-amber-500" />
      <p>{children}</p>
    </div>
  );
}

export function DashboardGrid() {
  const [preset, setPreset] = useState<PresetName>("Fire Monitoring");
  const [visible, setVisible] = useState<string[]>(PRESETS["Fire Monitoring"]);

  useEffect(() => {
    const saved = loadLayout();
    if (!saved) return;
    const timer = window.setTimeout(() => {
      setPreset(saved.preset);
      setVisible(saved.visible);
    }, 0);
    return () => window.clearTimeout(timer);
  }, []);

  function handlePresetChange(next: PresetName) {
    const nextVisible = PRESETS[next];
    setPreset(next);
    setVisible(nextVisible);
    saveLayout({ preset: next, visible: nextVisible });
  }

  function hideWidget(id: string) {
    const next = visible.filter((item) => item !== id);
    setVisible(next);
    saveLayout({ preset, visible: next });
  }

  const show = (id: string) => visible.includes(id);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-xs text-[hsl(var(--muted-foreground))] font-medium uppercase tracking-wide">
          Preset:
        </span>
        {(Object.keys(PRESETS) as PresetName[]).map((item) => (
          <button
            key={item}
            type="button"
            onClick={() => handlePresetChange(item)}
            className={`px-3 py-1 rounded-full text-xs font-medium transition-colors border ${
              preset === item
                ? "bg-emerald-500 text-white border-emerald-500"
                : "border-[hsl(var(--border))] text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--foreground))]"
            }`}
          >
            {item}
          </button>
        ))}
      </div>

      {show("metrics") && (
        <WidgetCard title="Operational Metrics" onClose={() => hideWidget("metrics")}>
          <MetricsBar />
        </WidgetCard>
      )}

      <div className="grid grid-cols-1 gap-4 dashboard-widget-grid">
        {show("activeFires") && preset === "Fire Monitoring" && (
          <WidgetCard title="Active Fires by Severity" onClose={() => hideWidget("activeFires")}>
            <UnavailableEvidence>
              Severity rollups are unavailable until a versioned warehouse aggregate is published.
            </UnavailableEvidence>
          </WidgetCard>
        )}

        {show("fireTimeline") && preset === "Fire Monitoring" && (
          <WidgetCard title="Fire Incidents This Week" onClose={() => hideWidget("fireTimeline")}>
            <UnavailableEvidence>
              Daily fire history is unavailable until accepted detections are pre-aggregated in the warehouse.
            </UnavailableEvidence>
          </WidgetCard>
        )}

        {show("fleetStatus") && preset === "Fleet Overview" && (
          <WidgetCard title="Fleet Status Distribution" onClose={() => hideWidget("fleetStatus")}>
            <UnavailableEvidence>
              No published fleet-status aggregate is available for this dashboard.
            </UnavailableEvidence>
          </WidgetCard>
        )}

        {show("routeActivity") && preset === "Fleet Overview" && (
          <WidgetCard title="Route Activity (24h)" onClose={() => hideWidget("routeActivity")}>
            <UnavailableEvidence>
              No published route-activity time series is available for this dashboard.
            </UnavailableEvidence>
          </WidgetCard>
        )}

        {show("sensorReadings") && preset === "Environmental" && (
          <WidgetCard title="Sensor Readings (24h)" onClose={() => hideWidget("sensorReadings")}>
            <UnavailableEvidence>
              Environmental sensor readings will appear after a validated time-series publication is available.
            </UnavailableEvidence>
          </WidgetCard>
        )}

        {show("landUse") && preset === "Environmental" && (
          <WidgetCard title="Land Use Distribution" onClose={() => hideWidget("landUse")}>
            <UnavailableEvidence>
              Land-use distribution is unavailable until a versioned regional aggregate is published.
            </UnavailableEvidence>
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
