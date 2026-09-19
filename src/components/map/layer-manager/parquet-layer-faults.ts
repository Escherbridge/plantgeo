/**
 * The fault/notice stack `LayerManager` raises over the map, as a pure function of plain data.
 *
 * Extracted from `LayerManager.tsx` on 2026-09-18 (style review W3, S12: that file was 1,727
 * lines against a ~600-line ceiling, and this array was ~220 of them). It is data orchestration
 * over state the component has already computed, so it moves out whole and becomes testable
 * without a map, a store or react-query. Every sentence below is byte-identical to the one it
 * replaced; the only change is where it lives.
 *
 * The inputs are deliberately NARROW -- booleans, strings and counts, never a react-query result
 * or a MapLibre handle -- so a test states a lane's condition directly and a future lane cannot
 * smuggle a fetch in here. Rationale: see `src/components/map/AGENTS.md` section "The fault and
 * notice stack".
 */

import type { ParquetLayerFault } from "@/components/map/ParquetLayerFaultBanner";
import type { BotanicalOccurrencesPhase } from "@/hooks/useBotanicalOccurrences";

/** One wave-C Parquet lane's drawn state, reduced to what the stack asks about it. */
export interface ParquetLaneReport {
  layerId: string;
  isDrawn: boolean;
  /** The typed reader state, or undefined while nothing has answered. */
  state: string | undefined;
  /** True only for a `ready` answer the reader capped; a non-ready answer is never truncated. */
  truncated: boolean;
  /** The lane's subject in sentence case, e.g. "Evacuation zones". */
  subject: string;
}

/** The MTBS capture window, as `burn-severity`'s ready answer reports it. */
export interface BurnSeverityCaptureSnapshot {
  capturedThrough: string;
  availableDay: string;
  coveredYears: { from: number | string; to: number | string };
  partialFireYears: readonly (number | string)[];
}

/** The fire lane's five distinguishable outcomes, already read off `useParquetFireDetections`. */
export interface FireLaneReport {
  isDrawn: boolean;
  state: string;
  truncated: boolean;
  /** The governed-absence evidence sentence, quoted verbatim; null when the state is not absent. */
  absenceReason: string | null;
  /** True when `not_generated` names the whole lane rather than one day. */
  isLaneNeverWritten: boolean;
}

/**
 * Everything the occurrence plane's two lanes say, flattened.
 *
 * SINCE W8-D (2026-09-18) the two lanes are no longer symmetric at the detail band: the tRPC
 * lane only ever runs there for backward-compatible fields (it is disabled, so `resultState`
 * etc. are simply absent), and every field that is actually POPULATED at the detail band --
 * `withheldCount`, `hasDetailAnswer`, `detailTruncated`, `gbifReadPhase`, `gbifFeatureCount` --
 * is sourced from the proxy lane's answer instead. At the aggregate band the tRPC lane is still
 * the only one that runs, so `resultState`/`resultNote`/`isError`/`truncated` still describe it.
 */
export interface BotanicalLaneReport {
  /** Whether the tRPC read was issued at all -- the gate the tRPC-only entries below share. Now
   * true only at the aggregate band; see the interface doc above. */
  isQueryEnabled: boolean;
  band: string;
  /** False when the viewport could not be measured; `gbif-empty` may not speak without one. */
  hasViewportBbox: boolean;
  /** The tRPC answer's own state: `detail`, `aggregate`, `refused` or `unavailable`. Only ever
   * populated at the aggregate band now. */
  resultState: string | undefined;
  /** The service-authored note, quoted verbatim by the refusal and unavailable entries. */
  resultNote: string | null;
  isError: boolean;
  /** The tRPC aggregate answer's own cap; the proxy lane's detail-band cap is `detailTruncated`. */
  truncated: boolean;
  /** From the proxy lane's detail answer, independent of `isQueryEnabled`. */
  withheldCount: number;
  occurrencesVisible: boolean;
  gbifVisible: boolean;
  /** The proxy lane's own read phase, since GBIF now shares its detail-band request. */
  gbifReadPhase: BotanicalOccurrencesPhase;
  gbifFeatureCount: number;
  /** True when the proxy lane's `detail` answer is in hand; `gbif-empty` is a statement about one. */
  hasDetailAnswer: boolean;
  /** That detail answer's own cap, which picks between the two `gbif-empty` sentences. */
  detailTruncated: boolean;
  /** `describeBotanicalOccurrencesState`'s sentence for the proxy lane, or null when quiet. */
  viewportCaption: string | null;
  viewportPhase: BotanicalOccurrencesPhase;
}

export interface ParquetLayerFaultInput {
  burnSeverityEnabled: boolean;
  burnSnapshot: BurnSeverityCaptureSnapshot | undefined;
  wavecLanes: readonly ParquetLaneReport[];
  vegetationEnabled: boolean;
  vegetationUnavailable: boolean;
  weatherEnabled: boolean;
  weatherUnavailable: boolean;
  fire: FireLaneReport;
  botanical: BotanicalLaneReport;
  /** The minimum zoom individual specimen points draw at, named in both floor sentences. */
  botanicalDetailMinZoom: number;
  /** The land-context viewport lane's single caption; see `useLandContextViewportBoundaries`. */
  landContextFault: ParquetLayerFault | null;
}

export function buildParquetLayerFaults(input: ParquetLayerFaultInput): ParquetLayerFault[] {
  const { fire, botanical } = input;
  return [
    input.burnSeverityEnabled && input.burnSnapshot
      ? {
          layerId: "burn-severity-capture",
          tone: "notice" as const,
          message: `MTBS captured ${input.burnSnapshot.capturedThrough}; available ${input.burnSnapshot.availableDay}. `
            + `Fire years ${input.burnSnapshot.coveredYears.from}–${input.burnSnapshot.coveredYears.to}. `
            + (input.burnSnapshot.partialFireYears.length
              ? `Mapping remains incomplete for ${input.burnSnapshot.partialFireYears.join(", ")}.`
              : "The captured query scope is complete."),
        }
      : null,
    ...input.wavecLanes.map((lane) =>
      lane.isDrawn && lane.state === "upstream_unavailable"
        ? {
            layerId: lane.layerId,
            tone: "fault" as const,
            message: `${lane.subject} are temporarily unavailable from the data service.`,
          }
        : null
    ),
    // A `notice`, not a `fault`: the lane answered, and the answer is real geometry that stops
    // short of the row budget rather than an outage. Reusing the fire lane's own wording keeps
    // one sentence for "this shape is a subset" across every layer that can say it.
    ...input.wavecLanes.map((lane) =>
      lane.isDrawn && lane.truncated
        ? {
            layerId: `${lane.layerId}-truncated`,
            tone: "notice" as const,
            message: lane.layerId === "burn-severity"
              ? "Burn history is incomplete because some history is unpublished or a read limit was reached. Available published burn history boundaries are shown."
              : `The Parquet row budget was reached. The ${lane.subject.toLowerCase()} drawn are a subset of this viewport.`,
          }
        : null
    ),
    input.vegetationEnabled && input.vegetationUnavailable
      ? {
          layerId: "vegetation",
          tone: "fault" as const,
          message:
            "Measured vegetation observations are temporarily unavailable from the data service.",
        }
      : null,
    input.weatherEnabled && input.weatherUnavailable
      ? {
          layerId: "weather",
          tone: "fault" as const,
          message: "Weather observations are temporarily unavailable from the data service.",
        }
      : null,
    fire.isDrawn && fire.state === "upstream_unavailable"
      ? {
          layerId: "fire",
          tone: "fault" as const,
          message: "Fire detections are temporarily unavailable from the data service.",
        }
      : null,
    // The transport failed before the reader returned any state at all, so there is no typed
    // refusal to quote -- and an empty canvas beside a lit switch would read as "no fires".
    // A `fault` and not a `notice`: nothing about the lane was established.
    fire.isDrawn && fire.state === "request_failed"
      ? {
          layerId: "fire-request-failed",
          tone: "fault" as const,
          message:
            "The fire detections request failed before returning a state. No fallback is shown.",
        }
      : null,
    // Every accepted fire answer is asserted un-truncated; a truncated one is surfaced here
    // instead of being quietly drawn as the whole viewport's detections.
    fire.isDrawn && fire.truncated
      ? {
          layerId: "fire-truncated",
          tone: "notice" as const,
          message:
            "The Parquet row budget was reached. The fire detections drawn are a subset of this viewport.",
        }
      : null,
    // The two refusals an empty canvas cannot tell apart from "no fires burned here", and the
    // reason each is a `notice` rather than a `fault`: nothing is down. A governed absence is a
    // POSITIVE record that the upstream was checked and published nothing, so the reason it
    // carries is the evidence and is quoted verbatim -- the same sentence `FireDetails` shows,
    // because a reader looking at the map and a reader looking at the dock must not be told two
    // different things about one day.
    fire.isDrawn && fire.state === "absent"
      ? {
          layerId: "fire-absent",
          tone: "notice" as const,
          message: `The fire lane recorded a governed absence for this day: ${
            fire.absenceReason ?? "reason unavailable"
          }.`,
        }
      : null,
    // `not_generated` is the opposite claim: nobody checked. Named by which silence it is --
    // one day missing from a written lane, or a lane that has never been written at all --
    // because "no detections" would assert an observation neither one made.
    fire.isDrawn && fire.state === "not_generated"
      ? {
          layerId: "fire-not-generated",
          tone: "notice" as const,
          message: fire.isLaneNeverWritten
            ? "The fire lane has never been written, so no detections can be drawn for any day."
            : "This day has not been written for the fire lane, so no detections can be drawn for it.",
        }
      : null,
    // The occurrence plane's own two non-answers, surfaced because an empty canvas beside a lit
    // switch reads as "no specimens were ever collected here" -- which is the one thing a
    // collection-bias layer must never imply.
    //
    // Both are a `notice`, not a `fault`, and the split is the same one the fire lane makes: the
    // service ANSWERED in both cases. `refused` is a governed refusal (a request the plane
    // declines to serve -- too wide a bbox, a filter combination it will not honour) and
    // `unavailable` is the plane reporting that no generation is published. Neither is an
    // outage, so neither is dressed as one. The service-authored `note` is quoted verbatim for
    // the same reason the fire lane quotes its evidence: the plane's own words are what a reader
    // can act on, and paraphrasing them would put this component in the business of explaining a
    // refusal it did not make. A genuine transport fault throws in the procedure instead and
    // reaches the map as a failed query, not as a state here.
    botanical.isQueryEnabled && botanical.resultState === "refused"
      ? {
          layerId: "botanical-refused",
          tone: "notice" as const,
          message: `The specimen occurrence plane declined this request: ${botanical.resultNote}`,
        }
      : null,
    botanical.isQueryEnabled && botanical.resultState === "unavailable"
      ? {
          layerId: "botanical-unavailable",
          tone: "notice" as const,
          message: `Specimen occurrences are not published: ${botanical.resultNote}`,
        }
      : null,
    botanical.isQueryEnabled && botanical.isError
      ? {
          layerId: "botanical-request-failed",
          tone: "fault" as const,
          message: "The botanical and GBIF occurrence request failed. Current viewport results could not be verified.",
        }
      : null,
    // A `notice` for the same reason every other lane's is: the records drawn are real, they
    // just stop short of the viewport. Saying so is what keeps a capped read from looking like
    // a collecting gap -- which, for this plane specifically, is a claim about where botanists
    // have and have not been.
    botanical.isQueryEnabled && botanical.truncated
      ? {
          layerId: "botanical-truncated",
          tone: "notice" as const,
          message:
            botanical.band === "detail"
              ? "The specimen row budget was reached. The occurrences drawn are a subset of this viewport."
              : "The cell budget was reached. The support cells drawn are a subset of this viewport.",
        }
      : null,
    // Withheld records are a POSITIVE fact the plane reports and the map cannot show: a specimen
    // whose locality is protected has no dot, and without this line its absence is
    // indistinguishable from it never having been collected.
    //
    // NOT gated on `isQueryEnabled` (the tRPC-lane gate): withheld counts are sourced from the
    // PROXY answer (W8-D, 2026-09-18), which runs at the detail band while the tRPC lane does
    // not. `withheldCount` is already zero whenever neither lane has answered, so the count alone
    // is the correct gate.
    botanical.withheldCount > 0
      ? {
          layerId: "botanical-withheld",
          tone: "notice" as const,
          message: `${botanical.withheldCount} specimen records in this release have their locality withheld by the publisher and cannot be drawn anywhere.`,
        }
      : null,
    // The Occurrences toggle is switched on but the map is below the detail floor, so nothing is
    // drawn and nothing was even fetched (`isQueryEnabled` is false in that case, since the
    // detail layer cannot draw at this band). Without this line a reader who turned the toggle on
    // at a continental zoom sees an empty map and no explanation -- indistinguishable from the
    // layer being broken.
    botanical.occurrencesVisible && botanical.band !== "detail"
      ? {
          layerId: "botanical-below-detail-floor",
          tone: "notice" as const,
          message: `Individual specimen points draw at zoom ${input.botanicalDetailMinZoom} and above. Zoom in to see them, or turn on Herbarium Specimen Richness / Collection Evidence & Effort for this zoom.`,
        }
      : null,
    botanical.gbifVisible && botanical.band !== "detail"
      ? {
          layerId: "gbif-below-detail-floor",
          tone: "notice" as const,
          message: `GBIF occurrence points draw at zoom ${input.botanicalDetailMinZoom} and above. Zoom in to see published records.`,
        }
      : null,
    // Only the settled returned slice supports an empty notice; see AGENTS.md §GBIF feedback.
    // "Settled" is decided by `gbifReadPhase`, which is now the PROXY lane's own phase verbatim
    // (W8-D, 2026-09-18: GBIF reads the same detail-band request the UBC layer does), so this
    // entry and `botanical-viewport-read` below can never disagree about when a read has landed.
    // The message itself stays authored here: it is a statement about the GBIF SLICE of a shared
    // answer, which the lane-wide vocabulary has no sentence for.
    botanical.gbifVisible &&
    botanical.band === "detail" &&
    botanical.hasViewportBbox &&
    (botanical.gbifReadPhase === "success" || botanical.gbifReadPhase === "empty") &&
    botanical.hasDetailAnswer &&
    botanical.gbifFeatureCount === 0
      ? {
          layerId: "gbif-empty",
          tone: "notice" as const,
          message: botanical.detailTruncated
            ? "No GBIF occurrence points appear in this limited result. The row limit prevents a complete assessment of this viewport and its current filters."
            : "No GBIF occurrence points were returned for this viewport and current filters.",
        }
      : null,
    // What the proxy lane reports about the UBC detail layer, in the one wording
    // `describeBotanicalOccurrencesState` owns -- including "a coarser rung answered than this
    // zoom asked for", which is the visible half of the 2026-09-18 rung-select decision. A
    // `notice`: a rung substitution and a stale frame are both real answers, not outages.
    botanical.occurrencesVisible &&
    botanical.band === "detail" &&
    botanical.viewportCaption !== null
      ? {
          layerId: "botanical-viewport-read",
          tone: botanical.viewportPhase === "error" ? ("fault" as const) : ("notice" as const),
          message: botanical.viewportCaption,
        }
      : null,
    // The land-context viewport lane's caption, built where that lane is read so every one of its
    // states -- including a failed read and a region that binds no source -- reaches a reader.
    input.landContextFault,
  ].filter((fault): fault is ParquetLayerFault => fault !== null);
}
