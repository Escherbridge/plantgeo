"use client";

import { useEffect, useRef } from "react";
import { LAND_CONTEXT_GROUP_LABELS, useLandContextStore } from "@/stores/land-context-store";

/**
 * The pinned, persistent detail panel. Per spec: click/tap/explicit keyboard selection "pins a
 * persistent detail panel" and the user must be able to "Browse multiple features without
 * losing the selected project area." This panel reads `candidateIndex` into `results` (a
 * cursor, not a single `selectedFeature`) so Next/Previous moves between overlapping features
 * without ever clearing `selection` or `results`.
 *
 * Only "Place details" + "Evidence and time" + a stub "Documented help" section are wired
 * against the placeholder reader for now (see `useLandContextQuery.ts`) -- "Relevant parties",
 * "Why this route applies" and "Related advice" need real relationship/contact data this
 * worker's stub does not have, and are left as clearly-labelled TODO sections rather than
 * fabricated content, per the spec's ban on inventing contact capability.
 *
 * Focus restoration: `triggerRef` records the element that had focus when the panel opened
 * (the map canvas is not focusable in a useful way, so callers should pass the originating
 * button/feature-list-item ref via `restoreFocusTo`). Explicit Close always returns focus there.
 */
interface LandContextDetailPanelProps {
  /** Element to restore focus to on close -- typically the feature-list button or search result
   * that triggered the selection. Optional: falls back to the panel's own close button parent. */
  restoreFocusTo?: HTMLElement | null;
}

export function LandContextDetailPanel({ restoreFocusTo = null }: LandContextDetailPanelProps) {
  const panelOpen = useLandContextStore((state) => state.panelOpen);
  const closePanel = useLandContextStore((state) => state.closePanel);
  const results = useLandContextStore((state) => state.results);
  const resultMeta = useLandContextStore((state) => state.resultMeta);
  const candidateIndex = useLandContextStore((state) => state.candidateIndex);
  const focusNextCandidate = useLandContextStore((state) => state.focusNextCandidate);
  const focusPreviousCandidate = useLandContextStore((state) => state.focusPreviousCandidate);
  const selection = useLandContextStore((state) => state.selection);

  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const feature = candidateIndex !== null ? results[candidateIndex] ?? null : null;

  useEffect(() => {
    if (panelOpen) closeButtonRef.current?.focus();
  }, [panelOpen]);

  function handleClose() {
    closePanel();
    restoreFocusTo?.focus();
  }

  useEffect(() => {
    if (!panelOpen) return;
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") handleClose();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [panelOpen]);

  if (!panelOpen) return null;

  return (
    <section
      role="dialog"
      aria-modal="false"
      aria-label="Land context selection details"
      className="land-context-detail-panel"
    >
      <header className="land-context-detail-panel-header">
        <h2>Selection details</h2>
        <button ref={closeButtonRef} type="button" onClick={handleClose}>
          Close
        </button>
      </header>

      {selection?.mode === "area" ? (
        <p className="land-context-detail-panel-coverage">
          {resultMeta
            ? `${resultMeta.returnedCount} of ${resultMeta.totalCount} intersecting feature(s) shown${
                resultMeta.hasMore ? " (more available -- refine the area for a complete list)" : ""
              }${resultMeta.partialCoverage ? "; selected area only partially covered by admitted sources" : ""}`
            : "Coverage not yet available for this area."}
        </p>
      ) : null}

      {results.length > 1 ? (
        <nav className="land-context-detail-panel-candidates" aria-label="Overlapping features">
          <button type="button" onClick={focusPreviousCandidate}>
            Previous candidate
          </button>
          <span aria-live="polite">
            {candidateIndex !== null ? candidateIndex + 1 : "–"} of {results.length}
          </span>
          <button type="button" onClick={focusNextCandidate}>
            Next candidate
          </button>
        </nav>
      ) : null}

      {feature ? (
        <div className="land-context-detail-panel-body">
          <section aria-labelledby="land-context-place-details-heading">
            <h3 id="land-context-place-details-heading">Place details</h3>
            <p>{feature.title}</p>
            <p>{LAND_CONTEXT_GROUP_LABELS[feature.group]}</p>
            {feature.category ? <p>{feature.category}</p> : null}
          </section>

          <section aria-labelledby="land-context-evidence-heading">
            <h3 id="land-context-evidence-heading">Evidence and time</h3>
            <p>{feature.sourceVintage ?? "Source vintage not yet available."}</p>
            <p>
              {feature.contactVerified === undefined
                ? "Contact verification status unknown."
                : feature.contactVerified
                  ? "Contact route recently verified."
                  : "Contact route not recently verified -- treat as stale."}
            </p>
          </section>

          <section aria-labelledby="land-context-help-heading">
            <h3 id="land-context-help-heading">Documented help</h3>
            <p>{feature.contactRouteSummary ?? "No documented contact route available yet."}</p>
          </section>

          {/* TODO(sibling worker: reference-plane relationships/contacts): "Relevant parties",
              "Why this route applies" and "Related advice" sections belong here once the real
              reader supplies office/program relationship data. Do not fabricate placeholder
              office names or claim a contact capability the stub cannot back. */}
        </div>
      ) : (
        <p>No candidate selected yet.</p>
      )}

      {/*
       * MOBILE / ACCESSIBLE AREA-SELECTION NOTE (not implemented here, time-boxed):
       * On touch devices a pointer-drag bounded-area draw (as a sibling area-selection control
       * would offer on desktop) has no direct touch-only or switch-access equivalent. The
       * accessible alternative belongs as a distinct control near wherever area selection is
       * triggered: e.g. "Draw project area" opens a step-by-step corner-tap or coordinate-entry
       * flow (tap to place each vertex, an explicit "Finish area" action, live vertex count
       * announced via aria-live) instead of requiring a continuous drag gesture. That control is
       * out of scope for this map/interaction-layer worker; this comment marks where it plugs
       * into `useLandContextStore.setSelection({ mode: "area", areaPolygon })`.
       */}
    </section>
  );
}
