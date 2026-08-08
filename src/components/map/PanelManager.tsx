"use client";

import dynamic from "next/dynamic";
import { useSession } from "next-auth/react";
import { useEffect, useState } from "react";
import {
  Flame,
  Droplets,
  Leaf,
  Mountain,
  Users,
  Building2,
  BarChart3,
  Layers,
  X,
} from "lucide-react";
import { panelIdsOwningLayers } from "@/lib/map/layer-registry";
import { useMap } from "@/lib/map/map-context";
import { useAuthStore } from "@/stores/auth-store";
import { useMapStore } from "@/stores/map-store";
import {
  usePanelStore,
  usePanelHasActiveLayers,
  getLayersForPanel,
  type PanelId,
} from "@/stores/panel-store";
import { useMapQueryPoint } from "@/hooks/useMapQueryPoint";
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
      // The emerald dot is the visual "this closed panel still governs a live layer"
      // signal; the name change is its non-visual equivalent.
      aria-label={hasActive ? `${label} (layers active)` : label}
      onClick={() => togglePanel(id)}
      // h-11 w-11 (44px): the previous h-9 (36px) fell under a comfortable mobile tap
      // target for the rail that opens every data panel.
      className={[
        // shrink-0 is load-bearing: as a flex item with `min-height: auto` this button
        // otherwise squashes toward its 16px icon on a short viewport instead of letting the
        // rail overflow, silently trading away the 44px tap target the h-11 exists for.
        "relative flex h-11 w-11 shrink-0 items-center justify-center rounded-md shadow-md transition-colors",
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

/**
 * The rail's own button for the left-edge layer tree, kept at the top so the surface that
 * governs EVERY layer sits above the seven that each report on one category.
 */
function LayerPanelButton() {
  const layerPanelOpen = usePanelStore((s) => s.layerPanelOpen);
  const toggleLayerPanel = usePanelStore((s) => s.toggleLayerPanel);

  return (
    <button
      type="button"
      title="Layers"
      aria-label="Layers"
      aria-expanded={layerPanelOpen}
      onClick={toggleLayerPanel}
      className={[
        "flex h-11 w-11 shrink-0 items-center justify-center rounded-md shadow-md transition-colors",
        "bg-[hsl(var(--background))] text-[hsl(var(--foreground))]",
        "hover:bg-[hsl(var(--accent))] hover:text-[hsl(var(--accent-foreground))]",
        layerPanelOpen ? "ring-2 ring-[hsl(var(--primary))] bg-[hsl(var(--primary)/0.1)]" : "",
      ].join(" ")}
    >
      <Layers className="h-4 w-4" />
    </button>
  );
}

/** Marks the rail hint as seen forever; opening any panel counts as having found the rail. */
const RAIL_HINT_DISMISSED_KEY = "plantgeo-rail-hint-dismissed";

/**
 * First-visit pointer at the rail. No data layer is on by default and the rail is seven
 * unlabeled icons, so a new visitor faces a blank map with nothing saying where the data
 * lives. Shown once: dismissed by its own close button or by opening any panel, and never
 * again after that (localStorage). Starts hidden and flips visible in an effect so the
 * server render and the first client paint agree.
 */
function RailHint() {
  const openPanel = usePanelStore((s) => s.openPanel);
  const [isVisible, setIsVisible] = useState(false);

  useEffect(() => {
    setIsVisible(localStorage.getItem(RAIL_HINT_DISMISSED_KEY) === null);
  }, []);

  useEffect(() => {
    if (openPanel !== null && isVisible) {
      localStorage.setItem(RAIL_HINT_DISMISSED_KEY, "1");
      setIsVisible(false);
    }
  }, [openPanel, isVisible]);

  if (!isVisible) return null;

  return (
    <div
      className="absolute left-[calc(var(--layer-panel-inset,0px)+4.25rem)] top-1/2 z-10 flex -translate-y-1/2 items-center gap-1.5 rounded-xl border border-(--glass-border) bg-(--glass-bg) py-1.5 pl-3 pr-1.5 text-xs text-[hsl(var(--foreground))] shadow-(--shadow-lg) [backdrop-filter:blur(var(--glass-blur))]"
      data-testid="rail-hint"
    >
      Turn on data layers here
      <button
        type="button"
        aria-label="Dismiss"
        onClick={() => {
          localStorage.setItem(RAIL_HINT_DISMISSED_KEY, "1");
          setIsVisible(false);
        }}
        className="flex h-6 w-6 items-center justify-center rounded-md text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--foreground))] max-sm:h-11 max-sm:w-11"
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}

export default function PanelManager() {
  const openPanel = usePanelStore((s) => s.openPanel);
  const closePanel = usePanelStore((s) => s.closePanel);
  const viewport = useMapStore((s) => s.viewport);
  // TeamDashboard was mounted with a hardcoded `teamId={null}`, which permanently disabled
  // its query: the Teams rail button opened a sheet that could only ever say it had failed
  // to load, for every account including one that owns an organization.
  //
  // Resolved the way TeamSwitcher resolves it, store first and session second, rather than
  // from the store alone as CommunityPanel does. The store is seeded by TeamSwitcher, which
  // mounts inside UserMenu on /dashboard and never on the map -- so on this page the store
  // is still null for a user whose session carries an active organization, and reading it
  // alone would tell an org owner they have no org.
  const { activeTeamId: storeActiveTeamId } = useAuthStore();
  const { data: session } = useSession();
  const activeTeamId = storeActiveTeamId ?? session?.user?.activeTeamId ?? null;

  function handleOpenChange(id: PanelId, open: boolean) {
    if (!open && openPanel === id) {
      closePanel();
    }
  }

  // The same derivation LayerManager uses, so a panel keys on the bbox AND the zoom the map
  // fetched. Both matter: zoom selects the SSURGO survey's render granularity server-side,
  // and a panel that omitted it would split the map's query entry in two.
  const { bbox, zoom } = useViewportBounds();

  // Map clicks become a query point only while the Soil panel is open -- the one panel with
  // a point query to answer. See src/components/map/AGENTS.md "Picking a point to query".
  const map = useMap();
  const isSoilPanelOpen = openPanel === "soil";
  const { queryPoint, clearQueryPoint } = useMapQueryPoint(map, isSoilPanelOpen);

  const mapCenter = { lat: viewport.latitude, lon: viewport.longitude };

  return (
    <>
      {/*
        The left icon rail.

        No `overflow-y-auto` and no `max-h-[70vh]`, and both removals are fixes rather than
        simplifications. `overflow-y-auto` emits only `overflow-y`, and per CSS Overflow 3
        §3.1 the other axis's `visible` then computes to `auto` -- so this became a scroll
        container on BOTH axes. The container is absolutely positioned with no width, so its
        shrink-to-fit width is its widest child (44px), and the active-layer badge below is
        `-right-0.5`, putting its border box 2px past that. Chrome drew a full horizontal
        scrollbar across a 44px column for those 2px, appearing exactly when the first data
        layer was switched on. `pr-1` now gives the badge's outset somewhere to land.

        The vertical cap it was paired with never worked either: seven 44px buttons and six
        gaps is 344px, so a scrollbar needed a viewport under ~212px -- and without `shrink-0`
        the buttons squashed toward their icons long before that. Height is bounded by the
        container's own edges instead, which also ends the `vh`-inside-`dvh` mismatch.
      */}
      <div className="absolute left-[calc(var(--layer-panel-inset,0px)+0.75rem)] top-1/2 z-10 flex -translate-y-1/2 flex-col gap-1.5 pr-1 transition-[left] duration-200">
        <LayerPanelButton />
        {RAIL_PANEL_IDS.map((id) => (
          <PanelButton
            key={id}
            id={id}
            icon={PANEL_PRESENTATION[id].icon}
            label={PANEL_PRESENTATION[id].label}
          />
        ))}
      </div>
      <RailHint />

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
        open={isSoilPanelOpen}
        onOpenChange={(o) => handleOpenChange("soil", o)}
        bbox={bbox ?? undefined}
        zoom={zoom}
        queryPoint={queryPoint}
        onClearQueryPoint={clearQueryPoint}
      />
      <CommunityPanel
        open={openPanel === "community"}
        onOpenChange={(o) => handleOpenChange("community", o)}
        mapCenter={mapCenter}
        bbox={bbox ?? undefined}
      />
      <TeamDashboard
        teamId={activeTeamId}
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
