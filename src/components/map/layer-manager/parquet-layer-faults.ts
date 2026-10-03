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

export interface ParquetLayerFaultInput {
  burnSeverityEnabled: boolean;
  burnSnapshot: BurnSeverityCaptureSnapshot | undefined;
  wavecLanes: readonly ParquetLaneReport[];
  vegetationEnabled: boolean;
  vegetationUnavailable: boolean;
  weatherEnabled: boolean;
  weatherUnavailable: boolean;
  fire: FireLaneReport;
  /** The land-context viewport lane's single caption; see `useLandContextViewportBoundaries`. */
  landContextFault: ParquetLayerFault | null;
}

export function buildParquetLayerFaults(input: ParquetLayerFaultInput): ParquetLayerFault[] {
  const { fire } = input;
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
    // The land-context viewport lane's caption, built where that lane is read so every one of its
    // states -- including a failed read and a region that binds no source -- reaches a reader.
    input.landContextFault,
  ].filter((fault): fault is ParquetLayerFault => fault !== null);
}
