"use client";

import dynamic from "next/dynamic";
import {
  Flame,
  Droplets,
  Leaf,
  Mountain,
  Users,
  Building2,
  BarChart3,
} from "lucide-react";
import { panelIdsOwningLayers } from "@/lib/map/layer-registry";
import { useMapStore } from "@/stores/map-store";
import {
  usePanelStore,
  usePanelHasActiveLayers,
  getLayersForPanel,
  type PanelId,
} from "@/stores/panel-store";
import { useViewportBounds } from "@/hooks/useViewportProxiedLayers";

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
const TeamDashboard = dynamic(
  () => import("@/components/panels/TeamDashboard").then((m) => ({ default: m.TeamDashboard })),
  { ssr: false }
);
const AnalyticsDashboard = dynamic(
  () => import("@/components/panels/AnalyticsDashboard").then((m) => ({ default: m.AnalyticsDashboard })),
  { ssr: false }
);

/** Icon and label for every panel the rail can open. Exhaustive over PanelId by construction. */
const PANEL_PRESENTATION: Record<PanelId, { icon: React.ReactNode; label: string }> = {
  fire: { icon: <Flame className="h-4 w-4" />, label: "Fire Dashboard" },
  water: { icon: <Droplets className="h-4 w-4" />, label: "Water" },
  vegetation: { icon: <Leaf className="h-4 w-4" />, label: "Vegetation" },
  soil: { icon: <Mountain className="h-4 w-4" />, label: "Soil" },
  community: { icon: <Users className="h-4 w-4" />, label: "Community" },
  team: { icon: <Building2 className="h-4 w-4" />, label: "Teams" },
  analytics: { icon: <BarChart3 className="h-4 w-4" />, label: "Analytics" },
};

/** Panels that govern no layer of their own, so the registry cannot order them. */
const NON_LAYER_PANEL_IDS: PanelId[] = ["team", "analytics"];

/**
 * The rail's buttons: every layer-owning panel in registry declaration order, then the
 * panels that carry no layers. Deriving the first half means a new registry entry becomes
 * reachable without editing this file.
 */
const RAIL_PANEL_IDS: PanelId[] = [...panelIdsOwningLayers(), ...NON_LAYER_PANEL_IDS];

/**
 * Tooltip text: the panel's name, plus every layer it governs so the rail reads as a map of
 * them. Only truthful while each named layer really has a switch in that panel, which
 * src/__tests__/lib/map/layer-registry.test.ts asserts against the panel sources.
 */
function panelTooltip(id: PanelId, label: string): string {
  const layerNames = getLayersForPanel(id).map((layerId) => layerId.replace(/-/g, " "));
  return layerNames.length > 0 ? `${label}\n${layerNames.join(", ")}` : label;
}

function PanelButton({ id, icon, label }: { id: PanelId; icon: React.ReactNode; label: string }) {
  const openPanel = usePanelStore((s) => s.openPanel);
  const togglePanel = usePanelStore((s) => s.togglePanel);
  const hasActive = usePanelHasActiveLayers(id);

  return (
    <button
      title={panelTooltip(id, label)}
      aria-label={label}
      onClick={() => togglePanel(id)}
      className={[
        "relative flex h-9 w-9 items-center justify-center rounded-md shadow-md transition-colors",
        "bg-[hsl(var(--background))] text-[hsl(var(--foreground))]",
        "hover:bg-[hsl(var(--accent))] hover:text-[hsl(var(--accent-foreground))]",
        openPanel === id
          ? "ring-2 ring-[hsl(var(--primary))] bg-[hsl(var(--primary)/0.1)]"
          : "",
      ].join(" ")}
    >
      {icon}
      {hasActive && (
        <span className="absolute -top-0.5 -right-0.5 h-2.5 w-2.5 rounded-full bg-emerald-500 ring-2 ring-[hsl(var(--background))]" />
      )}
    </button>
  );
}

export default function PanelManager() {
  const openPanel = usePanelStore((s) => s.openPanel);
  const closePanel = usePanelStore((s) => s.closePanel);
  const viewport = useMapStore((s) => s.viewport);

  function handleOpenChange(id: PanelId, open: boolean) {
    if (!open && openPanel === id) {
      closePanel();
    }
  }

  // The same derivation LayerManager uses, so a panel keys on the bbox the map fetched.
  const { bbox } = useViewportBounds();

  const mapCenter = { lat: viewport.latitude, lon: viewport.longitude };

  return (
    <>
      {/* Floating toolbar on the left side */}
      <div className="absolute left-3 top-1/2 z-10 flex -translate-y-1/2 flex-col gap-1">
        {RAIL_PANEL_IDS.map((id) => (
          <PanelButton
            key={id}
            id={id}
            icon={PANEL_PRESENTATION[id].icon}
            label={PANEL_PRESENTATION[id].label}
          />
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
        bbox={bbox ?? undefined}
      />
      <VegetationPanel
        open={openPanel === "vegetation"}
        onOpenChange={(o) => handleOpenChange("vegetation", o)}
        bbox={bbox ?? undefined}
      />
      <SoilPanel
        open={openPanel === "soil"}
        onOpenChange={(o) => handleOpenChange("soil", o)}
        bbox={bbox ?? undefined}
      />
      <CommunityPanel
        open={openPanel === "community"}
        onOpenChange={(o) => handleOpenChange("community", o)}
        mapCenter={mapCenter}
        bbox={bbox ?? undefined}
      />
      <TeamDashboard
        teamId={null}
        open={openPanel === "team"}
        onOpenChange={(o) => handleOpenChange("team", o)}
      />
      <AnalyticsDashboard
        open={openPanel === "analytics"}
        onOpenChange={(o) => handleOpenChange("analytics", o)}
        bbox={bbox ?? undefined}
      />
    </>
  );
}
