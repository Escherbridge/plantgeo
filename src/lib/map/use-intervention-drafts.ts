"use client";

/** Signed-in-only draft/proposed intervention overlay: merges own submissions + others' proposed rows into one deduplicated GeoJSON `FeatureCollection`, own copy winning dedup since it carries real geometry. */

import { useMemo } from "react";
import { useSession } from "next-auth/react";
import { trpc } from "@/lib/trpc/client";
import type { InterventionCategory } from "@/lib/environmental/intervention";
import type { InterventionDetailRecord } from "@/lib/map/intervention-detail";

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
  reviewNote?: string | null;
  createdAt?: Date | string | null;
  updatedAt?: Date | string | null;
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
  description?: string | null;
  longitude: number | null;
  latitude: number | null;
  createdAt?: Date | string | null;
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

/**
 * The detail record for one overlay row, kept beside the GeoJSON rather than
 * inside its `properties`.
 *
 * MapLibre serializes feature properties into the tile pipeline, so the fields
 * the detail modal needs but the paint expressions do not (timestamps, the
 * reviewer note, the submitter) are held here instead of widening the painted
 * properties. This is what makes a drafts-overlay click cost zero network
 * round trips (NFR-1): the record is already in memory.
 */
function ownSubmissionRecord(row: OwnSubmissionRow): InterventionDetailRecord {
  const properties = row.properties ?? {};
  const geometry = properties.geometry;
  const asString = (value: unknown) =>
    typeof value === "string" ? value : null;
  return {
    id: row.id,
    name: asString(properties.name),
    type: asString(properties.type),
    category: toCategory(properties.category),
    status: row.status,
    description: asString(properties.description),
    geometry:
      geometry && typeof geometry === "object" && "type" in geometry
        ? (geometry as GeoJSON.Geometry)
        : null,
    submittedByUserId: asString(properties.submittedByUserId),
    submittedByTeamId: asString(properties.submittedByTeamId),
    createdAt: (row.createdAt as Date | string | null | undefined) ?? null,
    updatedAt: (row.updatedAt as Date | string | null | undefined) ?? null,
    // Only ever meaningful on a rejected row, exactly as
    // `contributions.rejectContribution` writes it.
    reviewNote:
      row.status === "rejected" ? asString(row.reviewNote) : null,
    hasFullGeometry: Boolean(
      geometry && typeof geometry === "object" && "type" in geometry
    ),
  };
}

/** `listProposed` projects a centroid and no authorship, so the record says so. */
function proposedRecord(row: ProposedRow): InterventionDetailRecord {
  return {
    id: row.id,
    name: row.name,
    type: row.type,
    category: toCategory(row.category),
    status: "pending_review",
    description: row.description ?? null,
    geometry:
      row.longitude === null || row.latitude === null
        ? null
        : { type: "Point", coordinates: [row.longitude, row.latitude] },
    submittedByUserId: null,
    submittedByTeamId: null,
    createdAt: row.createdAt ?? null,
    updatedAt: null,
    reviewNote: null,
    // A centroid, not the drawn shape: the modal labels it rather than passing
    // it off as the submitted geometry.
    hasFullGeometry: false,
  };
}

export interface UseInterventionDraftsOverlayResult {
  /** Empty (never `undefined`) while signed out, loading, or errored -- the overlay draws nothing. */
  geojson: InterventionDraftFeatureCollection;
  /** Full detail records by feature id, for click-to-inspect without a round trip. */
  recordsById: Map<string, InterventionDetailRecord>;
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

  // Same dedupe as the GeoJSON above, own copy winning: it is the one that
  // carries the drawn geometry and the submitter's own timestamps.
  const recordsById = useMemo<Map<string, InterventionDetailRecord>>(() => {
    const records = new Map<string, InterventionDetailRecord>();
    if (!isEnabled) return records;

    for (const row of (proposedQuery.data ?? []) as ProposedRow[]) {
      records.set(row.id, proposedRecord(row));
    }
    for (const row of (mySubmissionsQuery.data ?? []) as OwnSubmissionRow[]) {
      records.set(row.id, ownSubmissionRecord(row));
    }
    return records;
  }, [isEnabled, mySubmissionsQuery.data, proposedQuery.data]);

  return {
    geojson,
    recordsById,
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
