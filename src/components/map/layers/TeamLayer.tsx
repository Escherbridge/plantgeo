"use client";

import { GeoJsonLayer } from "@deck.gl/layers";
import type { Color } from "@deck.gl/core";
import type { Feature, Geometry } from "geojson";
import { DECK_DEFAULT_PROPS } from "@/lib/map/deck-config";

// ── Types ─────────────────────────────────────────────────────────────────────

export interface TeamRecord {
  id: string;
  name: string;
  slug: string | null;
  description: string | null;
  orgType: string | null;
  specialties: unknown;
  website: string | null;
  serviceArea: unknown;
  isVerified: boolean | null;
  createdAt: Date | null;
  memberCount: number;
}

interface TeamLayerProps {
  id?: string;
  data: TeamRecord[];
  onHover?: (info: { object?: TeamFeature; x: number; y: number }) => void;
  onClick?: (info: { object?: TeamFeature }) => void;
}

interface TeamProperties {
  id: string;
  name: string;
  orgType: string | null;
  specialties: string[];
  isVerified: boolean;
  memberCount: number;
}

export type TeamFeature = Feature<Geometry, TeamProperties>;

// ── Colors by org type ────────────────────────────────────────────────────────
// Fill: color at 0.2 opacity (alpha ~51)
// Hover fill: color at 0.4 opacity (alpha ~102)
// Stroke: color at 0.8 opacity (alpha ~204)

const ORG_FILL_COLORS: Record<string, Color> = {
  nonprofit:   [76,  175, 80,  51],  // green
  cooperative: [33,  150, 243, 51],  // blue
  business:    [255, 152, 0,   51],  // orange
  individual:  [156, 39,  176, 51],  // purple
  government:  [96,  125, 139, 51],  // blue-grey
};

const ORG_HOVER_FILL_COLORS: Record<string, Color> = {
  nonprofit:   [76,  175, 80,  102],
  cooperative: [33,  150, 243, 102],
  business:    [255, 152, 0,   102],
  individual:  [156, 39,  176, 102],
  government:  [96,  125, 139, 102],
};

const ORG_LINE_COLORS: Record<string, Color> = {
  nonprofit:   [76,  175, 80,  204],
  cooperative: [33,  150, 243, 204],
  business:    [255, 152, 0,   204],
  individual:  [156, 39,  176, 204],
  government:  [96,  125, 139, 204],
};

const DEFAULT_FILL: Color       = [156, 163, 175, 51];
const DEFAULT_HOVER_FILL: Color = [156, 163, 175, 102];
const DEFAULT_LINE: Color       = [156, 163, 175, 204];

// ── Helpers ───────────────────────────────────────────────────────────────────

function toTeamFeatures(teams: TeamRecord[]): TeamFeature[] {
  return teams
    .filter((t) => t.serviceArea != null)
    .map((t) => ({
      type: "Feature" as const,
      geometry: t.serviceArea as Geometry,
      properties: {
        id: t.id,
        name: t.name,
        orgType: t.orgType,
        specialties: Array.isArray(t.specialties) ? (t.specialties as string[]) : [],
        isVerified: t.isVerified ?? false,
        memberCount: t.memberCount,
      },
    }));
}

// ── Factory function (deck.gl pattern used by rest of codebase) ───────────────

export function createTeamLayer({
  id = "team-service-areas",
  data,
  onHover,
  onClick,
}: TeamLayerProps) {
  const features = toTeamFeatures(data);

  return new GeoJsonLayer<TeamProperties>({
    ...DECK_DEFAULT_PROPS,
    id,
    data: { type: "FeatureCollection", features } as unknown as string,
    filled: true,
    stroked: true,
    getFillColor: (f) => {
      const orgType = (f as TeamFeature).properties?.orgType ?? "";
      return ORG_FILL_COLORS[orgType] ?? DEFAULT_FILL;
    },
    getLineColor: (f) => {
      const orgType = (f as TeamFeature).properties?.orgType ?? "";
      return ORG_LINE_COLORS[orgType] ?? DEFAULT_LINE;
    },
    highlightColor: (f) => {
      const orgType = (f as unknown as { properties?: { orgType?: string } })?.properties?.orgType ?? "";
      return (ORG_HOVER_FILL_COLORS[orgType] ?? DEFAULT_HOVER_FILL) as unknown as number[];
    },
    getLineWidth: 2,
    lineWidthUnits: "pixels",
    lineWidthMinPixels: 1,
    pickable: true,
    onHover,
    onClick,
  });
}
