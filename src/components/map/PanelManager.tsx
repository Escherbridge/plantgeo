"use client";

import { useState } from "react";
import dynamic from "next/dynamic";
import {
  Flame,
  Droplets,
  Leaf,
  Mountain,
  Users,
  Lightbulb,
  Building2,
  BarChart3,
} from "lucide-react";
import { useMapStore } from "@/stores/map-store";

const FireDashboard = dynamic(
  () => import("@/components/panels/FireDashboard").then((m) => ({ default: m.FireDashboard })),
  { ssr: false }
);
const WaterPanel = dynamic(
  () => import("@/components/panels/WaterPanel").then((m) => ({ default: m.WaterPanel })),
  { ssr: false }
);
const VegetationPanel = dynamic(
  () => import("@/components/panels/VegetationPanel").then((m) => ({ default: m.VegetationPanel })),
  { ssr: false }
);
const SoilPanel = dynamic(
  () => import("@/components/panels/SoilPanel").then((m) => ({ default: m.SoilPanel })),
  { ssr: false }
);
const CommunityPanel = dynamic(
  () => import("@/components/panels/CommunityPanel").then((m) => ({ default: m.CommunityPanel })),
  { ssr: false }
);
const StrategyPanel = dynamic(
  () => import("@/components/panels/StrategyPanel").then((m) => ({ default: m.StrategyPanel })),
  { ssr: false }
);
const TeamDashboard = dynamic(
  () => import("@/components/panels/TeamDashboard").then((m) => ({ default: m.TeamDashboard })),
  { ssr: false }
);
const AnalyticsDashboard = dynamic(
  () => import("@/components/panels/AnalyticsDashboard").then((m) => ({ default: m.AnalyticsDashboard })),
  { ssr: false }
);

type PanelId =
  | "fire"
  | "water"
  | "vegetation"
  | "soil"
  | "community"
  | "strategy"
  | "team"
  | "analytics";

const PANEL_BUTTONS: { id: PanelId; icon: React.ReactNode; label: string }[] = [
  { id: "fire", icon: <Flame className="h-4 w-4" />, label: "Fire Dashboard" },
  { id: "water", icon: <Droplets className="h-4 w-4" />, label: "Water" },
  { id: "vegetation", icon: <Leaf className="h-4 w-4" />, label: "Vegetation" },
  { id: "soil", icon: <Mountain className="h-4 w-4" />, label: "Soil" },
  { id: "community", icon: <Users className="h-4 w-4" />, label: "Community" },
  { id: "strategy", icon: <Lightbulb className="h-4 w-4" />, label: "Strategy" },
  { id: "team", icon: <Building2 className="h-4 w-4" />, label: "Teams" },
  { id: "analytics", icon: <BarChart3 className="h-4 w-4" />, label: "Analytics" },
];

export default function PanelManager() {
  const [openPanel, setOpenPanel] = useState<PanelId | null>(null);
  const { viewport } = useMapStore();

  function toggle(id: PanelId) {
    setOpenPanel((prev) => (prev === id ? null : id));
  }

  function handleOpenChange(id: PanelId, open: boolean) {
    if (!open && openPanel === id) setOpenPanel(null);
  }

  // Compute bbox string from current viewport for panels that need it
  const zoom = viewport.zoom ?? 8;
  const degPerPixel = 360 / Math.pow(2, zoom + 8);
  const halfW = degPerPixel * 512;
  const halfH = degPerPixel * 256;
  const bbox = [
    (viewport.longitude - halfW).toFixed(4),
    (viewport.latitude - halfH).toFixed(4),
    (viewport.longitude + halfW).toFixed(4),
    (viewport.latitude + halfH).toFixed(4),
  ].join(",");

  const mapCenter = { lat: viewport.latitude, lon: viewport.longitude };

  return (
    <>
      {/* Floating toolbar on the left side */}
      <div className="absolute left-3 top-1/2 z-10 flex -translate-y-1/2 flex-col gap-1">
        {PANEL_BUTTONS.map(({ id, icon, label }) => (
          <button
            key={id}
            title={label}
            onClick={() => toggle(id)}
            className={[
              "flex h-9 w-9 items-center justify-center rounded-md shadow-md transition-colors",
              "bg-[hsl(var(--background))] text-[hsl(var(--foreground))]",
              "hover:bg-[hsl(var(--accent))] hover:text-[hsl(var(--accent-foreground))]",
              openPanel === id
                ? "ring-2 ring-[hsl(var(--primary))] bg-[hsl(var(--primary)/0.1)]"
                : "",
            ].join(" ")}
          >
            {icon}
          </button>
        ))}
      </div>

      {/* Panels */}
      <FireDashboard
        open={openPanel === "fire"}
        onOpenChange={(o) => handleOpenChange("fire", o)}
        centerLat={viewport.latitude}
        centerLon={viewport.longitude}
      />
      <WaterPanel
        open={openPanel === "water"}
        onOpenChange={(o) => handleOpenChange("water", o)}
        bbox={bbox}
      />
      <VegetationPanel
        open={openPanel === "vegetation"}
        onOpenChange={(o) => handleOpenChange("vegetation", o)}
        bbox={bbox}
      />
      <SoilPanel
        open={openPanel === "soil"}
        onOpenChange={(o) => handleOpenChange("soil", o)}
      />
      <CommunityPanel
        open={openPanel === "community"}
        onOpenChange={(o) => handleOpenChange("community", o)}
        mapCenter={mapCenter}
        bbox={bbox}
      />
      <StrategyPanel
        open={openPanel === "strategy"}
        onOpenChange={(o) => handleOpenChange("strategy", o)}
        lat={viewport.latitude}
        lon={viewport.longitude}
      />
      <TeamDashboard
        teamId={null}
        open={openPanel === "team"}
        onOpenChange={(o) => handleOpenChange("team", o)}
      />
      <AnalyticsDashboard
        open={openPanel === "analytics"}
        onOpenChange={(o) => handleOpenChange("analytics", o)}
        bbox={bbox}
      />
    </>
  );
}
