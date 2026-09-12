"use client";

import { useEffect, useRef, useState } from "react";
import { trpc } from "@/lib/trpc/client";
import { useMap } from "@/lib/map/map-context";
import { InterventionBoundaryEditor } from "@/components/map/InterventionBoundaryEditor";
import { finishBoundary, type InterventionSiteGeometry, type BoundaryMode, type BoundaryPoint } from "@/lib/map/intervention-boundary";

/** Mirrors InterventionType in src/lib/environmental/intervention.ts. */
const INTERVENTION_TYPES = [
  { value: "reforestation", label: "Reforestation" },
  { value: "silvopasture", label: "Silvopasture" },
  { value: "cover_cropping", label: "Cover Cropping" },
  { value: "biochar", label: "Biochar" },
  { value: "keyline", label: "Keyline Design" },
] as const;

type InterventionType = (typeof INTERVENTION_TYPES)[number]["value"];

interface InterventionSubmitModalProps {
  /** Suggested location for an explicitly selected point. */
  lat: number;
  lon: number;
  teamId?: string;
  workspaceName?: string;
  onClose: () => void;
  onSuccess?: () => void;
  revision?: { featureId: string; name: string; type: string; description?: string; geometry?: InterventionSiteGeometry };
}

/** Captures a contributor's site geometry and explicit publication consent. */
export function InterventionSubmitModal({
  lat,
  lon,
  teamId,
  workspaceName,
  onClose,
  onSuccess,
  revision,
}: InterventionSubmitModalProps) {
  const map = useMap();
  const [interventionType, setInterventionType] =
    useState<InterventionType>(INTERVENTION_TYPES.find((entry) => entry.value === revision?.type)?.value ?? "reforestation");
  const [name, setName] = useState(revision?.name ?? "");
  const [description, setDescription] = useState(revision?.description ?? "");
  const [geometry, setGeometry] = useState<InterventionSiteGeometry | null>(revision?.geometry ?? null);
  const complexGeometry = geometry?.type === "MultiPolygon" || (geometry?.type === "Polygon" && geometry.coordinates.length > 1);
  const [drawingMode, setDrawingMode] = useState<BoundaryMode | null>(null);
  const [publicationConsent, setPublicationConsent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLElement>(null);

  useEffect(() => {
    const previouslyFocused = document.activeElement;
    closeButtonRef.current?.focus();
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
      if (event.key === "Tab" && dialogRef.current) {
        const controls = Array.from(dialogRef.current.querySelectorAll<HTMLElement>("button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled)"));
        const current = controls.indexOf(document.activeElement as HTMLElement);
        if ((event.shiftKey && current <= 0) || (!event.shiftKey && current === controls.length - 1)) {
          event.preventDefault(); controls[event.shiftKey ? controls.length - 1 : 0]?.focus();
        }
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      if (previouslyFocused instanceof HTMLElement) previouslyFocused.focus();
    };
  }, [onClose]);
  useEffect(() => { if (!drawingMode) closeButtonRef.current?.focus(); }, [drawingMode]);

  const submitMutation = trpc.interventions.submitIntervention.useMutation({
    onSuccess: () => {
      onSuccess?.();
      onClose();
    },
    onError: (err) => {
      setError(err.message);
    },
  });
  const reviseMutation = trpc.interventions.reviseIntervention.useMutation({
    onSuccess: () => { onSuccess?.(); onClose(); },
    onError: (err) => setError(err.message),
  });
  const isPending = submitMutation.isPending || reviseMutation.isPending;

  function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    if (name.trim().length < 3) {
      setError("Site name must be at least 3 characters.");
      return;
    }
    if (!publicationConsent) {
      setError("Confirm the review and publication notice before submitting.");
      return;
    }
    if (!geometry) {
      setError("Draw a site boundary or explicitly choose a point before submitting.");
      return;
    }
    try {
      if (geometry.type !== "MultiPolygon" && !complexGeometry) finishBoundary(geometry.type === "Point" ? "point" : "polygon", geometry.type === "Point" ? [geometry.coordinates as BoundaryPoint] : geometry.coordinates[0].slice(0, -1) as BoundaryPoint[]);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Choose a valid site geometry.");
      return;
    }
    const input = {
      name: name.trim(),
      type: interventionType,
      description: description.trim() || undefined,
      geometry,
      publicationConsent: true as const,
    };
    if (revision) reviseMutation.mutate({ ...input, featureId: revision.featureId });
    else submitMutation.mutate({ ...input, teamId });
  }

  if (drawingMode) return <InterventionBoundaryEditor mode={drawingMode} initial={geometry?.type === "MultiPolygon" || complexGeometry ? null : geometry} onCancel={() => setDrawingMode(null)} onSave={(site) => { setGeometry(site); setDrawingMode(null); setError(null); }} />;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <section
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="intervention-submit-title"
        className="bg-[hsl(var(--background))] border border-[hsl(var(--border))] rounded-xl shadow-xl w-full max-w-md max-h-[90dvh] overflow-y-auto mx-4 p-6"
      >
        <div className="flex items-center justify-between mb-4">
          <h2
            id="intervention-submit-title"
            className="text-lg font-semibold text-[hsl(var(--foreground))]"
          >
            {revision ? "Revise your recommendation" : "Recommend an Intervention"}
          </h2>
          <button
            ref={closeButtonRef}
            type="button"
            onClick={onClose}
            className="min-h-11 min-w-11 text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--foreground))] transition-colors"
            aria-label="Close intervention recommendation form"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <fieldset className="mb-4 rounded-lg border p-3">
          <legend className="text-sm font-medium">Site location or boundary</legend>
          <p role="status" className="mb-2 text-xs">{geometry ? geometry.type === "Point" ? `Point: ${geometry.coordinates[1].toFixed(4)}, ${geometry.coordinates[0].toFixed(4)}` : complexGeometry ? "Existing boundary with multiple parts or holes retained." : `Polygon boundary: ${geometry.coordinates[0].length - 1} vertices` : "No site chosen. Draw a boundary or choose a point."}</p>
          {complexGeometry && <p className="mb-2 text-xs">You can resubmit this boundary unchanged. Drawing or choosing a new site replaces the entire existing boundary, including all parts and holes; cancel drawing to keep it.</p>}
          <div className="flex flex-wrap gap-2">
            <button type="button" className="min-h-11 rounded border px-3 text-sm disabled:opacity-40" disabled={!map} onClick={() => setDrawingMode("polygon")}>{complexGeometry ? "Replace with polygon" : geometry?.type === "Polygon" ? "Edit polygon" : "Draw polygon"}</button>
            <button type="button" className="min-h-11 rounded border px-3 text-sm disabled:opacity-40" disabled={!map} onClick={() => setDrawingMode("rectangle")}>Draw rectangle</button>
            <button type="button" className="min-h-11 rounded border px-3 text-sm disabled:opacity-40" disabled={!map} onClick={() => setDrawingMode("point")}>Choose point on map</button>
            <button type="button" className="min-h-11 rounded border px-3 text-sm" onClick={() => setGeometry({ type: "Point", coordinates: [lon, lat] })}>Use map centre as point</button>
            {geometry && <button type="button" className="min-h-11 rounded border px-3 text-sm" onClick={() => setGeometry(null)}>Clear site</button>}
          </div>
          {!map && <p className="mt-2 text-xs">Map drawing is unavailable until the map is ready.</p>}
        </fieldset>

        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <div className="flex flex-col gap-1">
            <label
              htmlFor="intervention-type"
              className="text-sm font-medium text-[hsl(var(--foreground))]"
            >
              Intervention Type
            </label>
            <select
              id="intervention-type"
              value={interventionType}
              onChange={(event) =>
                setInterventionType(event.target.value as InterventionType)
              }
              className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] text-[hsl(var(--foreground))] px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-[hsl(var(--ring))]"
            >
              {INTERVENTION_TYPES.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>

          <div className="flex flex-col gap-1">
            <label
              htmlFor="intervention-name"
              className="text-sm font-medium text-[hsl(var(--foreground))]"
            >
              Site Name <span className="text-red-500">*</span>
            </label>
            <input
              id="intervention-name"
              type="text"
              value={name}
              onChange={(event) => setName(event.target.value)}
              maxLength={256}
              placeholder="Name for this intervention site"
              className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] text-[hsl(var(--foreground))] px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-[hsl(var(--ring))] placeholder:text-[hsl(var(--muted-foreground))]"
            />
          </div>

          <div className="flex flex-col gap-1">
            <label
              htmlFor="intervention-description"
              className="text-sm font-medium text-[hsl(var(--foreground))]"
            >
              Description
            </label>
            <textarea
              id="intervention-description"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              rows={3}
              maxLength={2000}
              placeholder="What is proposed here, and why (optional)"
              className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] text-[hsl(var(--foreground))] px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-[hsl(var(--ring))] placeholder:text-[hsl(var(--muted-foreground))] resize-none"
            />
          </div>

          <label className="flex items-start gap-2 rounded-lg border border-[hsl(var(--border))] p-3 text-sm text-[hsl(var(--foreground))]">
            <input
              type="checkbox"
              checked={publicationConsent}
              onChange={(event) => setPublicationConsent(event.target.checked)}
              className="mt-0.5"
            />
            <span>
              I understand this is a recommendation held for expert review
              {teamId
                ? ` and visible to members of ${
                    workspaceName ?? "the selected partner workspace"
                  }`
                : ""}
              . It does not appear on the public map unless a reviewer publishes
              it, and publication makes its location and boundary publicly visible.
            </span>
          </label>

          {error && (
            <p role="alert" className="text-sm text-red-600">
              {error}
            </p>
          )}

          <div className="flex gap-3 justify-end pt-1">
            <button
              type="button"
              onClick={onClose}
              className="min-h-11 px-4 py-2 rounded-lg border border-[hsl(var(--border))] text-sm text-[hsl(var(--foreground))] hover:bg-[hsl(var(--muted))] transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={isPending || !publicationConsent || !geometry}
              className="min-h-11 px-4 py-2 rounded-lg bg-[hsl(var(--primary))] text-[hsl(var(--primary-foreground))] text-sm font-medium hover:opacity-90 transition-opacity disabled:opacity-50"
            >
              {isPending
                ? "Submitting..."
                : "Submit Recommendation"}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}
