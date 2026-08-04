"use client";

import { useEffect, useRef, useState } from "react";
import { trpc } from "@/lib/trpc/client";

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
 * Point-only by design: no polygon drawing tool exists in this codebase yet, so
 * the map centre is the honest extent of what the user can express. The server
 * validator already accepts Polygon/MultiPolygon for whenever one lands.
 */
export function InterventionSubmitModal({
  lat,
  lon,
  teamId,
  workspaceName,
  onClose,
  onSuccess,
}: InterventionSubmitModalProps) {
  const [interventionType, setInterventionType] =
    useState<InterventionType>("reforestation");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [publicationConsent, setPublicationConsent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);

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
    submitMutation.mutate({
      name: name.trim(),
      type: interventionType,
      description: description.trim() || undefined,
      geometry: { type: "Point", coordinates: [lon, lat] },
      teamId,
      publicationConsent: true,
    });
  }

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
              disabled={submitMutation.isPending || !publicationConsent}
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
