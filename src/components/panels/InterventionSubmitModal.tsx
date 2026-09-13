"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
import maplibregl from "maplibre-gl";
import { trpc } from "@/lib/trpc/client";
import {
  LAND_INTERVENTION_TYPES,
  AIR_INTERVENTION_TYPES,
  type InterventionCategory,
  type InterventionType,
} from "@/lib/environmental/intervention";
import type { InterventionGeometry } from "@/lib/geo/intervention-geometry-schema";

const InterventionDrawControl = dynamic(
  () =>
    import("@/components/map/InterventionDrawControl").then(
      (mod) => mod.InterventionDrawControl
    ),
  { ssr: false }
);

/** Mirrors InterventionType in src/lib/environmental/intervention.ts. */
const INTERVENTION_TYPE_LABELS: Record<InterventionType, string> = {
  reforestation: "Reforestation",
  silvopasture: "Silvopasture",
  cover_cropping: "Cover Cropping",
  biochar: "Biochar",
  keyline: "Keyline Design",
  cloud_seeding: "Cloud Seeding",
};

const TYPES_BY_CATEGORY: Record<InterventionCategory, InterventionType[]> = {
  land: LAND_INTERVENTION_TYPES,
  air: AIR_INTERVENTION_TYPES,
};

/**
 * A Polygon must close (first position repeats the last) and describe at
 * least 3 distinct vertices (4 ring positions including the closing one).
 * Points always pass; MultiPolygon parts are checked the same way per ring.
 */
function validateDrawnGeometry(geometry: InterventionGeometry): string | null {
  const polygons =
    geometry.type === "Polygon"
      ? [geometry.coordinates]
      : geometry.type === "MultiPolygon"
        ? geometry.coordinates
        : null;
  if (polygons === null) return null;

  for (const polygon of polygons) {
    const ring = polygon[0] ?? [];
    if (ring.length < 4) {
      return "Draw at least 3 points, then close the polygon.";
    }
    const first = ring[0];
    const last = ring[ring.length - 1];
    if (first[0] !== last[0] || first[1] !== last[1]) {
      return "The drawn polygon must be closed.";
    }
  }
  return null;
}

interface InterventionSubmitModalProps {
  /** Map centre the recommendation is pinned to. */
  lat: number;
  lon: number;
  teamId?: string;
  workspaceName?: string;
  onClose: () => void;
  onSuccess?: () => void;
}

/**
 * Captures one signed-in contributor's own intervention recommendation.
 *
 * Drawing tool: an `InterventionDrawControl` (terra-draw wrapper, dynamically
 * imported client-side only) lets the contributor place a point or trace a
 * polygon on a small map centred on the pin; whatever they draw is what gets
 * submitted, not just the map centre. The server validator already accepts
 * Point/Polygon/MultiPolygon and enforces a per-category area cap.
 */
export function InterventionSubmitModal({
  lat,
  lon,
  teamId,
  workspaceName,
  onClose,
  onSuccess,
}: InterventionSubmitModalProps) {
  const [category, setCategory] = useState<InterventionCategory>("land");
  const [interventionType, setInterventionType] = useState<InterventionType>(
    TYPES_BY_CATEGORY.land[0]
  );
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [publicationConsent, setPublicationConsent] = useState(false);
  const [geometry, setGeometry] = useState<InterventionGeometry | null>(null);
  const [geometryError, setGeometryError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const mapContainerRef = useRef<HTMLDivElement>(null);
  const [map, setMap] = useState<maplibregl.Map | null>(null);

  const typeOptions = useMemo(() => TYPES_BY_CATEGORY[category], [category]);

  function handleCategoryChange(next: InterventionCategory) {
    setCategory(next);
    setInterventionType(TYPES_BY_CATEGORY[next][0]);
  }

  function handleGeometryChange(nextGeometry: InterventionGeometry | null) {
    if (nextGeometry === null) {
      setGeometry(null);
      setGeometryError(null);
      return;
    }
    const issue = validateDrawnGeometry(nextGeometry);
    setGeometryError(issue);
    setGeometry(issue ? null : nextGeometry);
  }

  useEffect(() => {
    if (!mapContainerRef.current) return;
    const instance = new maplibregl.Map({
      container: mapContainerRef.current,
      style: {
        version: 8,
        sources: {},
        layers: [
          {
            id: "background",
            type: "background",
            paint: { "background-color": "#e5e7eb" },
          },
        ],
      },
      center: [lon, lat],
      zoom: 14,
    });
    setMap(instance);
    return () => {
      instance?.remove?.();
      setMap(null);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- the drawing map centres once per open modal
  }, []);

  useEffect(() => {
    const previouslyFocused = document.activeElement;
    closeButtonRef.current?.focus();
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      if (previouslyFocused instanceof HTMLElement) previouslyFocused.focus();
    };
  }, [onClose]);

  const submitMutation = trpc.interventions.submitIntervention.useMutation({
    onSuccess: () => {
      onSuccess?.();
      onClose();
    },
    onError: (err) => {
      setError(err.message);
    },
  });

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
    if (geometry === null) {
      setError("Draw a point or polygon before submitting.");
      return;
    }
    submitMutation.mutate({
      name: name.trim(),
      type: interventionType,
      category,
      description: description.trim() || undefined,
      geometry,
      teamId,
      publicationConsent: true,
    });
  }

  const canSubmit =
    geometry !== null && geometryError === null && !submitMutation.isPending;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <section
        role="dialog"
        aria-modal="true"
        aria-labelledby="intervention-submit-title"
        className="bg-[hsl(var(--background))] border border-[hsl(var(--border))] rounded-xl shadow-xl w-full max-w-md mx-4 p-6"
      >
        <div className="flex items-center justify-between mb-4">
          <h2
            id="intervention-submit-title"
            className="text-lg font-semibold text-[hsl(var(--foreground))]"
          >
            Recommend an Intervention
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

        <p className="text-xs text-[hsl(var(--muted-foreground))] mb-4">
          Site location: {lat.toFixed(4)}, {lon.toFixed(4)} &middot; recentre the
          map to move the pin.
        </p>

        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <div className="flex flex-col gap-1">
            <label
              htmlFor="intervention-category"
              className="text-sm font-medium text-[hsl(var(--foreground))]"
            >
              Category
            </label>
            <select
              id="intervention-category"
              value={category}
              onChange={(event) =>
                handleCategoryChange(event.target.value as InterventionCategory)
              }
              className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] text-[hsl(var(--foreground))] px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-[hsl(var(--ring))]"
            >
              <option value="land">Land</option>
              <option value="air">Air</option>
            </select>
          </div>

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
              {typeOptions.map((type) => (
                <option key={type} value={type}>
                  {INTERVENTION_TYPE_LABELS[type]}
                </option>
              ))}
            </select>
          </div>

          <div className="flex flex-col gap-1">
            <span className="text-sm font-medium text-[hsl(var(--foreground))]">
              Draw Site Geometry <span className="text-red-500">*</span>
            </span>
            <div
              ref={mapContainerRef}
              className="h-48 w-full rounded-lg border border-[hsl(var(--border))] overflow-hidden"
            />
            {map && (
              <InterventionDrawControl
                map={map}
                onGeometryChange={handleGeometryChange}
              />
            )}
            {geometryError && (
              <p role="alert" className="text-sm text-red-600">
                {geometryError}
              </p>
            )}
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
              . It does not appear on the public map unless a reviewer approves
              it, and once approved its location becomes publicly visible.
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
              disabled={!canSubmit || !publicationConsent}
              className="min-h-11 px-4 py-2 rounded-lg bg-[hsl(var(--primary))] text-[hsl(var(--primary-foreground))] text-sm font-medium hover:opacity-90 transition-opacity disabled:opacity-50"
            >
              {submitMutation.isPending
                ? "Submitting..."
                : "Submit Recommendation"}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}
