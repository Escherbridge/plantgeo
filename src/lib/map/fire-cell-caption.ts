// The one caption for a published fire-detection cell, shared by the hover tooltip and the
// click popup. Pure formatting: no React, no maplibre, no DOM.

import { formatTimestampWithRelative, toIsoTimestamp } from "@/lib/map/time-format";

/** The heading both surfaces put above the lines below. */
export const FIRE_CELL_CAPTION_TITLE = "Fire detection cell";

/** What a cell says when its detections carried no radiative-power reading at all. */
export const FIRE_CELL_NO_FRP_VALUE = "Not reported";

/**
 * One rendered line of the caption, kept as label + value rather than as finished text so the
 * popup can bold the value and the tooltip can flatten it, without either owning the wording.
 */
export interface FireCellCaptionLine {
  /** Null for a line that reads as one phrase ("Aggregated at z9") rather than a labelled field. */
  label: string | null;
  value: string;
  /** Provenance rather than measurement; the popup renders these in its muted meta style. */
  meta: boolean;
}

/** The flattened one-line form, which is all the hover tooltip can render. */
export function fireCellCaptionText(line: FireCellCaptionLine): string {
  return line.label === null ? line.value : `${line.label}: ${line.value}`;
}

function toFiniteNumber(value: unknown): number | null {
  const num = typeof value === "number" ? value : typeof value === "string" ? Number(value) : NaN;
  return Number.isFinite(num) ? num : null;
}

function stringField(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  if (!trimmed) return null;
  const lower = trimmed.toLowerCase();
  if (lower === "null" || lower === "undefined" || lower === "nan") return null;
  return trimmed;
}

/**
 * The six lines a fire cell may show, in the order both surfaces render them.
 *
 * Two rules are load-bearing and are why this exists once rather than twice:
 *
 * - **No FRP reading is not zero radiative power.** `frpSum` is `0` for a cell whose detections
 *   all lacked a reading, so `frpObservationCount > 0` (and a non-null sum) is what licenses a
 *   number. Same condition the circle paint uses in `FireLayer.tsx`, so the colour and the
 *   caption can never disagree about which cells have power.
 * - **The FRP line is conditional on `detectionCount`.** It is the only line with a value for an
 *   absent field, so an unconditional one would turn a property bag carrying nothing at all into
 *   a one-line tooltip. Keyed on the count because a cell that exists always has one.
 */
export function fireDetectionCellLines(props: Record<string, unknown>): FireCellCaptionLine[] {
  const detections = toFiniteNumber(props.detectionCount);
  const highConfidence = toFiniteNumber(props.highConfidenceDetectionCount);
  const frpObservations = toFiniteNumber(props.frpObservationCount);
  const frpSum = toFiniteNumber(props.frpSum);
  const observedDay = stringField(props.observedDay);
  const newest = formatTimestampWithRelative(toIsoTimestamp(props.newestObservedAt));
  const tier = toFiniteNumber(props.zoomTier);

  const lines: FireCellCaptionLine[] = [];

  if (detections !== null) {
    lines.push({ label: "Detections", value: detections.toLocaleString(), meta: false });
  }
  if (highConfidence !== null) {
    lines.push({ label: "High confidence", value: highConfidence.toLocaleString(), meta: false });
  }
  if (detections !== null) {
    const measured = frpObservations !== null && frpObservations > 0 && frpSum !== null;
    lines.push({
      label: "Total FRP",
      value: measured
        ? `${frpSum.toLocaleString(undefined, {
            minimumFractionDigits: 1,
            maximumFractionDigits: 1,
          })} MW`
        : FIRE_CELL_NO_FRP_VALUE,
      meta: false,
    });
  }
  if (observedDay !== null) {
    lines.push({ label: "Observed", value: observedDay, meta: true });
  }
  if (newest !== null) {
    lines.push({ label: "Newest detection", value: newest, meta: true });
  }
  if (tier !== null) {
    lines.push({ label: null, value: `Aggregated at z${tier}`, meta: true });
  }

  return lines;
}
