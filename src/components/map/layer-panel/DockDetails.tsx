"use client";

/**
 * The details regions, and the map-derived props each one needs.
 *
 * Every region is dynamically imported and rendered ONLY while its section is expanded, which
 * is the whole reason the merge could fold seven right-hand sheets into one dock without
 * making the dock expensive to open: a collapsed section costs one chunk that was never
 * fetched and zero queries. Each region's own `enabled` flags therefore no longer carry an
 * `open &&` term -- being mounted IS being open.
 *
 * Each body owns the hooks its region needs, rather than the dock reading everything and
 * prop-drilling: `useViewportBounds` re-renders its caller on every pan, and scoping that to
 * the regions that key a query on the viewport keeps a pan from re-rendering the layer rows
 * above them.
 */

import { useMemo } from "react";
import dynamic from "next/dynamic";
import { useSession } from "next-auth/react";
import { useMapQueryPoint } from "@/hooks/useMapQueryPoint";
import { useViewportBounds } from "@/hooks/useViewportProxiedLayers";
import { useLayerVisibility } from "@/lib/map/layer-toggle-context";
import { useMap } from "@/lib/map/map-context";
import { useAuthStore } from "@/stores/auth-store";
import { useBotanicalOccurrenceStore } from "@/stores/botanical-occurrence-store";
import { useMapStore } from "@/stores/map-store";
import type { DockDetailsId } from "@/stores/panel-store";

const DetailsLoading = () => (
  <p role="status" className="px-1 py-2 text-[11px] text-[hsl(var(--muted-foreground))]">
    Loading…
  </p>
);

const FireDetails = dynamic(
  () => import("@/components/panels/FireDetails").then((m) => ({ default: m.FireDetails })),
  { ssr: false, loading: DetailsLoading }
);
const WaterDetails = dynamic(
  () => import("@/components/panels/WaterDetails").then((m) => ({ default: m.WaterDetails })),
  { ssr: false, loading: DetailsLoading }
);
const BotanicalFilters = dynamic(
  () =>
    import("@/components/panels/BotanicalFilters").then((m) => ({ default: m.BotanicalFilters })),
  { ssr: false, loading: DetailsLoading }
);
const BotanicalOccurrenceDetails = dynamic(
  () =>
    import("@/components/panels/BotanicalOccurrenceDetails").then((m) => ({
      default: m.BotanicalOccurrenceDetails,
    })),
  { ssr: false, loading: DetailsLoading }
);
const VegetationDetails = dynamic(
  () =>
    import("@/components/panels/VegetationDetails").then((m) => ({
      default: m.VegetationDetails,
    })),
  { ssr: false, loading: DetailsLoading }
);
const SoilDetails = dynamic(
  () => import("@/components/panels/SoilDetails").then((m) => ({ default: m.SoilDetails })),
  { ssr: false, loading: DetailsLoading }
);
const ClimateDetails = dynamic(
  () =>
    import("@/components/panels/ClimateDetails").then((m) => ({ default: m.ClimateDetails })),
  { ssr: false, loading: DetailsLoading }
);
const CommunityDetails = dynamic(
  () =>
    import("@/components/panels/CommunityDetails").then((m) => ({
      default: m.CommunityDetails,
    })),
  { ssr: false, loading: DetailsLoading }
);
const TeamDetails = dynamic(
  () => import("@/components/panels/TeamDetails").then((m) => ({ default: m.TeamDetails })),
  { ssr: false, loading: DetailsLoading }
);
const OfflinePanel = dynamic(
  () => import("@/components/panels/OfflinePanel").then((m) => ({ default: m.OfflinePanel })),
  { ssr: false, loading: DetailsLoading }
);

function FireDetailsBody() {
  // The centre, not the bbox: the weather cards read the nearest published observation to one
  // point, and a bbox would key a query the map's weather layer does not share.
  const latitude = useMapStore((state) => state.viewport.latitude);
  const longitude = useMapStore((state) => state.viewport.longitude);
  const center = useMemo(() => ({ lat: latitude, lon: longitude }), [latitude, longitude]);
  return <FireDetails center={center} />;
}

function WaterDetailsBody() {
  // The same derivation LayerManager uses, so this region and the map key one react-query
  // entry per proxied feed rather than two that merely look alike. Zoom is equally part of
  // the key now because it selects exactly one private Parquet serving rung.
  const { bbox, zoom } = useViewportBounds();
  return <WaterDetails bbox={bbox ?? undefined} zoom={zoom} />;
}

/**
 * Vegetation, plus the botanical specimen surfaces that file under the same section.
 *
 * The three botanical toggles live in the Vegetation category (`layer-registry.ts`), so their
 * filters and their selected-record panel belong to this region rather than to a dock section of
 * their own -- a new `PanelId` would demand its own title and its own body here, which is a
 * section nobody specified.
 *
 * Neither reads the plane: `BotanicalFilters` writes the store slice `LayerManager`'s query keys
 * on, and the record panel renders whatever that query already selected. That is what keeps the
 * map and this region on ONE read instead of two that could disagree about the generation.
 */
function VegetationDetailsBody() {
  const selectedOccurrence = useBotanicalOccurrenceStore((state) => state.selectedFeature);
  const visibility = useLayerVisibility();
  const anyBotanicalLayerOn =
    visibility["botanical-occurrences"] ||
    visibility["botanical-richness"] ||
    visibility["botanical-collection-effort"];
  return (
    <div className="flex flex-col gap-4">
      <VegetationDetails />
      {anyBotanicalLayerOn && (
        <>
          <BotanicalFilters />
          <BotanicalOccurrenceDetails feature={selectedOccurrence} />
        </>
      )}
    </div>
  );
}

function SoilDetailsBody() {
  // Both matter to the key: the bbox AND the zoom the map fetched, since zoom selects the
  // SSURGO survey's render granularity server-side.
  const { bbox, zoom } = useViewportBounds();
  // Map clicks become a query point while this region is mounted -- the one region with a
  // point query to answer. Collapsing it (or closing the dock) unmounts this body, which
  // disarms capture and drops the pin, exactly as closing the old Soil sheet did. See
  // src/components/map/AGENTS.md "Picking a point to query".
  const map = useMap();
  const { queryPoint, clearQueryPoint } = useMapQueryPoint(map, true);
  return (
    <SoilDetails
      bbox={bbox ?? undefined}
      zoom={zoom}
      queryPoint={queryPoint}
      onClearQueryPoint={clearQueryPoint}
    />
  );
}

function ClimateDetailsBody() {
  // Both matter to the key: zoom selects the one physical Parquet rung that answers.
  const { bbox, zoom } = useViewportBounds();
  return <ClimateDetails bbox={bbox ?? undefined} zoom={zoom} />;
}

function CommunityDetailsBody() {
  // No bbox: the panel stopped listing requests when they became public map features
  // (`public_strategy_requests_20260913` Phase 3), so nothing here is viewport-scoped any more.
  const latitude = useMapStore((state) => state.viewport.latitude);
  const longitude = useMapStore((state) => state.viewport.longitude);
  // Memoized because it is a prop object rebuilt on every pan; the region reads it for the
  // submit form's default place, not for a query key.
  const mapCenter = useMemo(() => ({ lat: latitude, lon: longitude }), [latitude, longitude]);
  return <CommunityDetails mapCenter={mapCenter} />;
}

function TeamDetailsBody() {
  // Store first, session second, the way TeamSwitcher resolves it. The store alone is not
  // enough here: it is seeded by TeamSwitcher, which mounts inside UserMenu on /dashboard and
  // never on the map -- so on this page reading it alone would tell an org owner they have no
  // org. (The predecessor of this region was mounted with a hardcoded `teamId={null}`, which
  // permanently disabled its query.)
  const storeActiveTeamId = useAuthStore((state) => state.activeTeamId);
  const { data: session } = useSession();
  return <TeamDetails teamId={storeActiveTeamId ?? session?.user?.activeTeamId ?? null} />;
}

function OfflineDetailsBody() {
  return <OfflinePanel />;
}

/** Exhaustive over `DockDetailsId`: a new section fails to compile rather than rendering blank. */
const DETAILS_BODIES: Record<DockDetailsId, () => React.ReactNode> = {
  fire: FireDetailsBody,
  water: WaterDetailsBody,
  vegetation: VegetationDetailsBody,
  soil: SoilDetailsBody,
  climate: ClimateDetailsBody,
  community: CommunityDetailsBody,
  team: TeamDetailsBody,
  offline: OfflineDetailsBody,
};

/** One details region, mounted by its section only while that section is expanded. */
export function DockDetailsBody({ id }: { id: DockDetailsId }) {
  const Body = DETAILS_BODIES[id];
  return <Body />;
}
