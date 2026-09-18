"use client";

/**
 * The automatic land-context viewport read, decoded, drawn and captioned.
 *
 * Before 2026-09-18 `LayerManager` mounted `useLandContextViewport` and read one field off it
 * (`state === "area_over_budget"`), so one tRPC round trip per pan was parsed and dropped and four
 * of the lane's states rendered nothing -- style review W3 blocker B1. This hook is the consumer
 * that closes it: it gates the fetch, decodes the answer through the CLICK lane's own
 * `toResults`/`toFeature` path, and returns a caption for every state the lane can be in.
 *
 * Rationale and the caption table: see `src/components/map/AGENTS.md` section
 * "The land-context viewport lane".
 */

import { useMemo } from "react";
import {
  useLandContextViewport,
  type LandContextViewportState,
} from "@/hooks/useLandContextViewport";
import {
  layerBindingInRegion,
  LAND_CONTEXT_REGION_LAYER_SLUG,
} from "@/lib/map/layer-region-binding";
import {
  statedCoverageStates,
  toResults,
  type BoundaryResult,
} from "@/components/map/land-context/land-context-features";
import { useLandContextStore, type LandContextFeature } from "@/stores/land-context-store";
import { useTimeSliderStore } from "@/stores/time-slider-store";
import type { CoverageState } from "@/lib/environmental/land-context-contract";
import type { ParquetLayerFault } from "@/components/map/ParquetLayerFaultBanner";

/** Which of the six read phases `typescript.md` asks a component to tell apart this lane is in. */
export type LandContextViewportReadPhase =
  | "idle"
  | "loading"
  | "stale"
  | "empty"
  | "partial"
  | "success"
  | "error";

export interface LandContextViewportBoundaries {
  /** Every drawable boundary the viewport read returned, in the click lane's own feature shape. */
  features: LandContextFeature[];
  /** Null until at least one feature exists, so the layer draws nothing rather than an empty set. */
  geoJSON: GeoJSON.FeatureCollection | null;
  /** Why the lane is or is not asking; `layer_unbound_in_region` means no request was issued. */
  state: LandContextViewportState;
  readPhase: LandContextViewportReadPhase;
  /** The one caption this lane raises into LayerManager's notice stack, or null when idle. */
  fault: ParquetLayerFault | null;
}

/** The plane's own words for "this region binds no source for me"; never dressed as an outage. */
const SOURCE_UNBOUND_FOR_REGION: CoverageState = "source_unbound_for_region";

const COVERAGE_STATE_SENTENCES: Readonly<Record<CoverageState, string>> = {
  matched: "boundaries matched",
  unknown_coverage: "coverage here is unknown, so nothing is drawn",
  no_match_in_proven_coverage: "coverage here is proven and holds no boundaries for this view",
  partial_area_coverage: "this view is only partly covered by admitted sources",
  outside_pilot: "this view is outside the pilot states (Washington, Oregon, Idaho)",
  source_unbound_for_region: "no admitted source is bound to them here",
  upstream_unavailable: "the read did not complete, so nothing is known about coverage here",
  unavailable_history: "history is unavailable; only the current reference can be shown",
};

/**
 * A read that did not complete is drawn as a FAULT, not as an amber governed absence (W4 S4, W5 S6).
 *
 * `upstream_unavailable` is the plane's own word for "the lookup failed", and it arrives INSIDE a
 * result that returned -- so the `query.isError` arm never sees it and every such view used to
 * render in the identical amber pill as "no source is bound here". Those two are the opposite
 * claim: one says the record is complete, the other says nothing is known.
 */
function toneForCoverage(states: CoverageState[]): ParquetLayerFault["tone"] {
  return states.includes("upstream_unavailable") ? "fault" : "notice";
}

function describeCoverageStates(states: CoverageState[]): string {
  const stated = states.filter((state) => state !== "matched");
  if (stated.length === 0) return "";
  return ` Stated: ${stated.map((state) => COVERAGE_STATE_SENTENCES[state]).join("; ")}.`;
}

/**
 * The boundary results a response carries, or an empty list for every non-`ok` shape.
 *
 * Narrowed structurally, exactly as `useLandContextQuery` narrows the same procedure's output:
 * the router answers `{ status: "ok", data }` or `{ status: "budget_exceeded", ... }`, and the
 * inferred tRPC type is not importable here without reaching into `@/lib/server/**`.
 */
function boundaryResultsFrom(data: unknown): BoundaryResult[] {
  if (data === null || typeof data !== "object" || !("status" in data)) return [];
  const response = data as { status: string; data?: unknown };
  return response.status === "ok" && Array.isArray(response.data)
    ? (response.data as BoundaryResult[])
    : [];
}

/** True when the response is the router's typed refusal rather than a list of results. */
function budgetRefusalReason(data: unknown): string | null {
  if (data === null || typeof data !== "object" || !("status" in data)) return null;
  const response = data as { status: string; reason?: string };
  return response.status === "budget_exceeded" ? (response.reason ?? "budget_exceeded") : null;
}

export function useLandContextViewportBoundaries(): LandContextViewportBoundaries {
  const enabledGroups = useLandContextStore((state) => state.enabledGroups);
  const capabilities = useTimeSliderStore((state) => state.capabilities);
  // The gate that makes "an unbound region issues no pan-reads" true, read once per render through
  // the SAME rule every layer toggle reads. `not_federated` is not an absence: a build whose
  // manifest has never heard of this slug must not have its reference plane switched off by that
  // silence, which is why the verdict is compared against `unbound` and not against `bound`.
  const isLayerBoundInRegion =
    layerBindingInRegion(capabilities, LAND_CONTEXT_REGION_LAYER_SLUG) !== "unbound";

  const lane = useLandContextViewport({ enabledGroups, isLayerBoundInRegion });
  const { state, query } = lane;

  return useMemo(() => {
    const results = boundaryResultsFrom(query.data);
    const features = toResults(results, enabledGroups);
    const coverageStates = statedCoverageStates(results);
    const geoJSON = features.length === 0 ? null : toFeatureCollection(features);

    const phase = readPhaseFor({
      state,
      isError: query.isError === true,
      hasResponse: query.data !== undefined,
      isRetained: query.isPlaceholderData === true,
      featureCount: features.length,
      coverageStates,
    });

    return {
      features,
      geoJSON,
      state,
      readPhase: phase,
      fault: captionFor({
        state,
        phase,
        featureCount: features.length,
        coverageStates,
        budgetRefusal: budgetRefusalReason(query.data),
      }),
    };
  }, [enabledGroups, state, query.data, query.isError, query.isPlaceholderData]);
}

interface ReadPhaseInput {
  state: LandContextViewportState;
  isError: boolean;
  hasResponse: boolean;
  isRetained: boolean;
  featureCount: number;
  coverageStates: CoverageState[];
}

function readPhaseFor(input: ReadPhaseInput): LandContextViewportReadPhase {
  if (input.isError) return "error";
  // Every non-`reading` state is a decision NOT to ask, so there is no read to be in a phase of.
  if (input.state !== "reading") return "idle";
  if (!input.hasResponse) return "loading";
  if (input.isRetained) return "stale";
  if (input.featureCount === 0) return "empty";
  // Features in hand AND a coverage statement beside them: real boundaries that stop short of
  // the whole answer, which `typescript.md`'s "partial results must remain partial" is about.
  return input.coverageStates.some((coverage) => coverage !== "matched") ? "partial" : "success";
}

interface CaptionInput {
  state: LandContextViewportState;
  phase: LandContextViewportReadPhase;
  featureCount: number;
  coverageStates: CoverageState[];
  budgetRefusal: string | null;
}

/**
 * One caption per lane state, and no state renders nothing except `no_group_enabled`.
 *
 * `no_group_enabled` is the single silence, and it is the only honest one: the reader has
 * switched nothing on, so there is no absence to explain. Every other branch -- including a read
 * that succeeded -- says what happened, because an empty map beside a lit toggle otherwise reads
 * as "there is nothing here", which for a land-ownership plane is the one claim it must not make.
 */
function captionFor(input: CaptionInput): ParquetLayerFault | null {
  switch (input.state) {
    case "no_group_enabled":
      return null;
    case "layer_unbound_in_region":
      return {
        layerId: "land-context-unbound-in-region",
        tone: "notice",
        message:
          "Land-context boundaries are not available in this region: no source is bound for them here, so this view is not read for them.",
      };
    case "viewport_unavailable":
      return {
        layerId: "land-context-viewport-unavailable",
        tone: "notice",
        message:
          "The current view could not be measured, so land-context boundaries were not read for it. Pan or zoom the map to read them.",
      };
    case "no_rung_serves_this_viewport":
      return {
        layerId: "land-context-no-rung-serves-viewport",
        tone: "notice",
        message:
          "No published land-context rung serves a view this wide, so nothing was read for it. Zoom in to read boundaries for this view.",
      };
    case "area_over_budget":
      return {
        layerId: "land-context-area-over-budget",
        tone: "notice",
        message:
          "Land-context boundaries load automatically for a viewport of about one square degree or smaller. Zoom in to read them for this view.",
      };
    case "reading":
      return readingCaption(input);
  }
}

function readingCaption(input: CaptionInput): ParquetLayerFault | null {
  if (input.phase === "error") {
    return {
      layerId: "land-context-request-failed",
      tone: "fault",
      message:
        "The land-context boundary read for this view failed before returning a state. No boundaries are drawn and none are implied.",
    };
  }
  if (input.budgetRefusal !== null) {
    return {
      layerId: "land-context-read-refused",
      tone: "notice",
      message: `The land-context plane declined to read this view: ${input.budgetRefusal}. Nothing is drawn.`,
    };
  }
  if (input.phase === "loading") {
    return {
      layerId: "land-context-reading",
      tone: "notice",
      message: "Reading land-context boundaries for this view.",
    };
  }
  if (input.phase === "stale") {
    return {
      layerId: "land-context-retained",
      tone: "notice",
      message:
        "Land-context boundaries drawn here are retained from the previous view while this one is read.",
    };
  }
  if (input.phase === "empty") {
    // The one caption this whole extraction exists for. An empty canvas beside a lit toggle
    // reads as "nobody owns this land"; the plane's own coverage statement is quoted instead,
    // and `source_unbound_for_region` -- today's only answer, because no land-context lane is
    // published (W2-C) -- is named as a governed absence rather than an outage.
    const isUnbound = input.coverageStates.includes(SOURCE_UNBOUND_FOR_REGION);
    return {
      layerId: isUnbound ? "land-context-source-unbound" : "land-context-empty",
      tone: toneForCoverage(input.coverageStates),
      message: isUnbound
        ? `No land-context boundaries are drawn for this view: no admitted source is bound to this plane here, so none were read.${describeCoverageStates(input.coverageStates)}`
        : `The land-context read for this view returned no boundaries.${describeCoverageStates(input.coverageStates)}`,
    };
  }
  if (input.phase === "partial") {
    return {
      layerId: "land-context-partial",
      tone: toneForCoverage(input.coverageStates),
      message: `${input.featureCount} land-context boundaries are drawn for this view, and they are not the whole answer.${describeCoverageStates(input.coverageStates)}`,
    };
  }
  if (input.phase === "success") {
    return {
      layerId: "land-context-drawn",
      tone: "notice",
      message: `${input.featureCount} land-context boundaries are drawn for this view.`,
    };
  }
  return null;
}

/** The MapLibre shape the viewport layer draws; property names match `LandContextLayer`'s. */
export function toFeatureCollection(features: LandContextFeature[]): GeoJSON.FeatureCollection {
  return {
    type: "FeatureCollection",
    features: features.map((feature) => ({
      type: "Feature",
      id: feature.id,
      properties: {
        featureId: feature.id,
        group: feature.group,
        title: feature.title,
        category: feature.category ?? null,
        sourceVintage: feature.sourceVintage ?? null,
        contactRouteSummary: feature.contactRouteSummary ?? null,
      },
      geometry: feature.geometry,
    })),
  };
}
