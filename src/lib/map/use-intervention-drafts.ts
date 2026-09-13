"use client";

/** Signed-in-only draft/proposed intervention overlay: merges own submissions + others' proposed rows into one deduplicated GeoJSON `FeatureCollection`, own copy winning dedup since it carries real geometry. */

import { useMemo } from "react";
import { useSession } from "next-auth/react";
import { trpc } from "@/lib/trpc/client";
import type { InterventionCategory } from "@/lib/environmental/intervention";

/** One drafted or proposed intervention feature's properties, as the overlay paints it. */
export interface InterventionDraftProperties {
  id: string;
  name: string | null;
  type: string | null;
  category: InterventionCategory | null;
  status: string;
  /** True when the signed-in caller authored this row themselves. */
  isOwn: boolean;
}

export type InterventionDraftFeature = GeoJSON.Feature<
  GeoJSON.Geometry,
  InterventionDraftProperties
>;

export type InterventionDraftFeatureCollection = GeoJSON.FeatureCollection<
  GeoJSON.Geometry,
  InterventionDraftProperties
>;

const EMPTY_COLLECTION: InterventionDraftFeatureCollection = {
  type: "FeatureCollection",
  features: [],
};

function toCategory(value: unknown): InterventionCategory | null {
  return value === "land" || value === "air" ? value : null;
}

interface OwnSubmissionRow {
  id: string;
  status: string;
  properties: Record<string, unknown> | null;
}

/** `listMySubmissions` carries the drawn geometry, so its own copy always wins a dedupe. */
function ownSubmissionFeature(row: OwnSubmissionRow): InterventionDraftFeature | null {
  const geometry = row.properties?.geometry;
  if (!geometry || typeof geometry !== "object" || !("type" in geometry)) return null;

  return {
    type: "Feature",
    geometry: geometry as GeoJSON.Geometry,
    properties: {
      id: row.id,
      name: typeof row.properties?.name === "string" ? row.properties.name : null,
      type: typeof row.properties?.type === "string" ? row.properties.type : null,
      category: toCategory(row.properties?.category),
      status: row.status,
      isOwn: true,
    },
  };
}

interface ProposedRow {
  id: string;
  name: string | null;
  type: string | null;
  category: string | null;
  longitude: number | null;
  latitude: number | null;
}

/** `listProposed` only ever carries a centroid -- there is no other geometry to draw here. */
function proposedFeature(row: ProposedRow): InterventionDraftFeature | null {
  if (row.longitude === null || row.latitude === null) return null;

  return {
    type: "Feature",
    geometry: { type: "Point", coordinates: [row.longitude, row.latitude] },
    properties: {
      id: row.id,
      name: row.name,
      type: row.type,
      category: toCategory(row.category),
      status: "pending_review",
      isOwn: false,
    },
  };
}

export interface UseInterventionDraftsOverlayResult {
  /** Empty (never `undefined`) while signed out, loading, or errored -- the overlay draws nothing. */
  geojson: InterventionDraftFeatureCollection;
  isLoading: boolean;
  isError: boolean;
  /** False for a signed-out reader; nothing is fetched and the overlay is inert. */
  isEnabled: boolean;
}

/** The merged, deduplicated, signed-in-gated overlay feed; both queries stay disabled while signed out. */
export function useInterventionDraftsOverlay(): UseInterventionDraftsOverlayResult {
  const { status } = useSession();
  const isEnabled = status === "authenticated";

  const mySubmissionsQuery = trpc.interventions.listMySubmissions.useQuery(
    {},
    { enabled: isEnabled, retry: false }
  );
  const proposedQuery = trpc.interventions.listProposed.useQuery(
    {},
    { enabled: isEnabled, retry: false }
  );

  const geojson = useMemo<InterventionDraftFeatureCollection>(() => {
    if (!isEnabled) return EMPTY_COLLECTION;

    const ownRows = (mySubmissionsQuery.data ?? []) as OwnSubmissionRow[];
    const proposedRows = (proposedQuery.data ?? []) as ProposedRow[];

    const ownFeatures: InterventionDraftFeature[] = [];
    const ownIds = new Set<string>();
    for (const row of ownRows) {
      ownIds.add(row.id);
      const feature = ownSubmissionFeature(row);
      if (feature) ownFeatures.push(feature);
    }

    const proposedFeatures: InterventionDraftFeature[] = [];
    for (const row of proposedRows) {
      // The caller's own row already has a (better) copy from listMySubmissions above.
      if (ownIds.has(row.id)) continue;
      const feature = proposedFeature(row);
      if (feature) proposedFeatures.push(feature);
    }

    return {
      type: "FeatureCollection",
      features: [...ownFeatures, ...proposedFeatures],
    };
  }, [isEnabled, mySubmissionsQuery.data, proposedQuery.data]);

  return {
    geojson,
    isLoading: isEnabled && (mySubmissionsQuery.isLoading || proposedQuery.isLoading),
    isError: isEnabled && (mySubmissionsQuery.isError || proposedQuery.isError),
    isEnabled,
  };
}

/** Invalidates both underlying queries so the overlay refetches without a full page reload. */
export async function invalidateInterventionDraftsOverlay(
  utils: {
    interventions: {
      listMySubmissions: { invalidate: () => Promise<void> };
      listProposed: { invalidate: () => Promise<void> };
    };
  }
): Promise<void> {
  await Promise.all([
    utils.interventions.listMySubmissions.invalidate(),
    utils.interventions.listProposed.invalidate(),
  ]);
}
