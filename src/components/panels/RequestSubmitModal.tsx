"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { trpc } from "@/lib/trpc/client";
import type { InterventionType } from "@/lib/environmental/intervention";
import {
  INTERVENTION_TYPE_LABELS,
  TYPES_BY_CATEGORY,
} from "@/lib/environmental/intervention-form";
import type { InterventionGeometry } from "@/lib/geo/intervention-geometry-schema";

interface RequestSubmitModalProps {
  /** Map centre the request is pinned to; recentring the map moves the pin. */
  lat: number;
  lon: number;
  onClose: () => void;
  onSuccess?: () => void;
}

/** Publishes a contributor request at the map centre; see panels/AGENTS.md. */
export function RequestSubmitModal({
  lat,
  lon,
  onClose,
  onSuccess,
}: RequestSubmitModalProps) {
  const [requestType, setRequestType] = useState<InterventionType>(
    TYPES_BY_CATEGORY.land[0]
  );
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [publicationConsent, setPublicationConsent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);

  // Land-category only (OQ-D): nothing in the request flow reaches an air-category type, so the
  // category picker `InterventionSubmitModal` shows has no counterpart here.
  const typeOptions = TYPES_BY_CATEGORY.land;

  /** The map centre, promoted to the real geometry the mutation requires. */
  const geometry = useMemo<InterventionGeometry>(
    () => ({ type: "Point", coordinates: [lon, lat] }),
    [lat, lon]
  );

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

  const submitMutation = trpc.interventions.submitRequest.useMutation({
    onSuccess: () => {
      onSuccess?.();
      onClose();
    },
    onError: (err) => {
      setError(err.message);
    },
  });

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (title.trim().length < 3) {
      setError("Title must be at least 3 characters.");
      return;
    }
    if (!publicationConsent) {
      setError("Confirm that this request may be published before submitting.");
      return;
    }
    submitMutation.mutate({
      name: title.trim(),
      type: requestType,
      description: description.trim() || undefined,
      geometry,
      publicationConsent: true,
    });
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 sm:p-4">
      <section
        role="dialog"
        aria-modal="true"
        aria-labelledby="strategy-request-title"
        className="flex h-full w-full flex-col overflow-hidden bg-[hsl(var(--background))] shadow-xl sm:h-auto sm:max-h-[90vh] sm:w-full sm:max-w-md sm:rounded-xl sm:border sm:border-[hsl(var(--border))]"
      >
        <div className="flex items-center justify-between border-b border-[hsl(var(--border))] p-4 sm:border-none sm:p-6 sm:pb-0">
          <h2 id="strategy-request-title" className="text-lg font-semibold text-[hsl(var(--foreground))]">
            Request a Strategy Here
          </h2>
          <button
            ref={closeButtonRef}
            type="button"
            onClick={onClose}
            className="min-h-11 min-w-11 text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--foreground))] transition-colors"
            aria-label="Close strategy request form"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-4 sm:p-6">
        <p className="text-xs text-[hsl(var(--muted-foreground))] mb-4">
          Pinned at {lat.toFixed(4)}, {lon.toFixed(4)} &middot; recentre the map to
          move the pin. Your request goes on the public map straight away, with no
          review queue. Anyone can read the published request without signing in.
          Sign in to read comments; contributor access is required to add comments
          or likes.
        </p>

        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <div className="flex flex-col gap-1">
            <label htmlFor="strategy-request-type" className="text-sm font-medium text-[hsl(var(--foreground))]">
              Strategy Type
            </label>
            <select
              id="strategy-request-type"
              value={requestType}
              onChange={(e) => setRequestType(e.target.value as InterventionType)}
              className="min-h-11 rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] text-[hsl(var(--foreground))] px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-[hsl(var(--ring))]"
            >
              {typeOptions.map((type) => (
                <option key={type} value={type}>
                  {INTERVENTION_TYPE_LABELS[type]}
                </option>
              ))}
            </select>
          </div>

          <div className="flex flex-col gap-1">
            <label htmlFor="strategy-request-title-input" className="text-sm font-medium text-[hsl(var(--foreground))]">
              Title <span className="text-red-500">*</span>
            </label>
            <input
              id="strategy-request-title-input"
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              maxLength={200}
              placeholder="Brief title for this request"
              className="min-h-11 rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] text-[hsl(var(--foreground))] px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-[hsl(var(--ring))] placeholder:text-[hsl(var(--muted-foreground))]"
            />
          </div>

          <div className="flex flex-col gap-1">
            <label htmlFor="strategy-request-description" className="text-sm font-medium text-[hsl(var(--foreground))]">
              Description
            </label>
            <textarea
              id="strategy-request-description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={3}
              maxLength={2000}
              placeholder="Describe why this area needs intervention (optional)"
              className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] text-[hsl(var(--foreground))] px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-[hsl(var(--ring))] placeholder:text-[hsl(var(--muted-foreground))] resize-none"
            />
          </div>

          {/* The same explicit consent gate `submitIntervention` uses, and for the same reason: a
              request is exactly as public as a published intervention, so it takes the identical
              gate rather than a weaker one. `publicationConsent: true` is a literal the server
              re-checks -- unticking this box cannot be routed around by the client. */}
          <label className="flex items-start gap-2 rounded-lg border border-[hsl(var(--border))] p-3 text-sm text-[hsl(var(--foreground))]">
            <input
              type="checkbox"
              checked={publicationConsent}
              onChange={(event) => setPublicationConsent(event.target.checked)}
              className="mt-0.5"
            />
            <span>
              I understand this request is published immediately: its location, title
              and description appear on the public map for anyone to read, including
              people who are not signed in.
            </span>
          </label>

          {error && (
            <p role="alert" className="text-sm text-red-600">{error}</p>
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
              {submitMutation.isPending ? "Posting..." : "Post Request"}
            </button>
          </div>
        </form>
        </div>
      </section>
    </div>
  );
}
