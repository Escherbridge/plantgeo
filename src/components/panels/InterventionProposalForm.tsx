"use client";

import { useMemo, useState, type RefObject } from "react";
import dynamic from "next/dynamic";
import type { Map as MapLibreMap } from "maplibre-gl";
import { trpc } from "@/lib/trpc/client";
import type {
  InterventionCategory,
  InterventionType,
} from "@/lib/environmental/intervention";
import {
  INTERVENTION_TYPE_LABELS,
  TYPES_BY_CATEGORY,
  validateDrawnGeometry,
} from "@/lib/environmental/intervention-form";
import type { InterventionGeometry } from "@/lib/geo/intervention-geometry-schema";
import { useInterventionDraftStore } from "@/stores/intervention-draft-store";

const InterventionDrawControl = dynamic(
  () =>
    import("@/components/map/InterventionDrawControl").then(
      (mod) => mod.InterventionDrawControl
    ),
  { ssr: false }
);

export interface InterventionProposalFormProps {
  /** The point the proposal is pinned to, as seeded into the draft store. */
  lat: number;
  lon: number;
  teamId?: string;
  workspaceName?: string;
  /**
   * The drawing map, created and OWNED by the caller (the workspace shell). Passed in rather
   * than created here because this form is hidden -- never unmounted -- when the workspace
   * switches modes, and an instance created on this component's mount would still be torn down
   * by any future change that made the slot conditional. `null` until the shell's mount effect
   * has run.
   */
  map: MapLibreMap | null;
  /** Where the shell wants the drawing canvas painted. Rendered by this form, owned by the shell. */
  mapContainerRef: RefObject<HTMLDivElement | null>;
  /** Fired after a successful submission, once the draft has been cleared. */
  onSubmitted?: () => void;
}

/**
 * The intervention-proposal pane's body: the draw control, the form, and the submit.
 *
 * All of its field state lives in `intervention-draft-store.ts` rather than in `useState`, which
 * is what lets the workspace hide this pane (mode switch) or the user walk away from it without
 * losing a half-written proposal. `clearDraft()` is called in exactly ONE place -- the
 * mutation's `onSuccess` below -- so nothing else can silently discard unsubmitted work.
 *
 * `InterventionSubmitModal.tsx` is the OTHER copy of this form: the community "+Recommend"
 * button's modal, which deliberately keeps its own local state so opening it never adopts (or
 * clobbers) the map workspace's in-progress draft. The two share their labels, category table
 * and geometry validator via `src/lib/environmental/intervention-form.ts`.
 */
export function InterventionProposalForm({
  lat,
  lon,
  teamId,
  workspaceName,
  map,
  mapContainerRef,
  onSubmitted,
}: InterventionProposalFormProps) {
  const category = useInterventionDraftStore((state) => state.category);
  const interventionType = useInterventionDraftStore((state) => state.interventionType);
  const name = useInterventionDraftStore((state) => state.name);
  const description = useInterventionDraftStore((state) => state.description);
  const publicationConsent = useInterventionDraftStore((state) => state.publicationConsent);
  const geometry = useInterventionDraftStore((state) => state.geometry);
  const geometryError = useInterventionDraftStore((state) => state.geometryError);
  const setCategory = useInterventionDraftStore((state) => state.setCategory);
  const setInterventionType = useInterventionDraftStore((state) => state.setInterventionType);
  const setName = useInterventionDraftStore((state) => state.setName);
  const setDescription = useInterventionDraftStore((state) => state.setDescription);
  const setPublicationConsent = useInterventionDraftStore(
    (state) => state.setPublicationConsent
  );
  const setGeometry = useInterventionDraftStore((state) => state.setGeometry);
  const setGeometryError = useInterventionDraftStore((state) => state.setGeometryError);
  const clearDraft = useInterventionDraftStore((state) => state.clearDraft);

  /** Submission-time complaints only. Not draft state: there is nothing here worth persisting. */
  const [error, setError] = useState<string | null>(null);

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
    // `setGeometry` clears the error itself, so the error write must come second.
    setGeometry(issue ? null : nextGeometry);
    setGeometryError(issue);
  }

  const submitMutation = trpc.interventions.submitIntervention.useMutation({
    onSuccess: () => {
      // The ONE automatic `clearDraft()`: the work is now the server's, so keeping it here
      // would only offer the user a duplicate submission.
      clearDraft();
      onSubmitted?.();
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
    <div className="p-3">
      <p className="mb-3 text-xs text-[hsl(var(--muted-foreground))]">
        Site location: {lat.toFixed(4)}, {lon.toFixed(4)} &middot; draw on the map below to set
        the exact area.
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
            className="min-h-11 rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] px-3 py-2 text-sm text-[hsl(var(--foreground))] focus:outline-none focus:ring-2 focus:ring-[hsl(var(--ring))]"
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
            className="min-h-11 rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] px-3 py-2 text-sm text-[hsl(var(--foreground))] focus:outline-none focus:ring-2 focus:ring-[hsl(var(--ring))]"
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
          {/* The shell's map paints here. This node must stay in the tree for the whole
              workspace session -- removing it removes the canvas the live instance renders into. */}
          <div
            ref={mapContainerRef}
            data-testid="intervention-draw-map"
            className="h-56 w-full overflow-hidden rounded-lg border border-[hsl(var(--border))]"
          />
          {map && (
            <InterventionDrawControl
              map={map}
              initialGeometry={geometry}
              onGeometryChange={handleGeometryChange}
              onRestoreError={setGeometryError}
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
            className="min-h-11 rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] px-3 py-2 text-sm text-[hsl(var(--foreground))] placeholder:text-[hsl(var(--muted-foreground))] focus:outline-none focus:ring-2 focus:ring-[hsl(var(--ring))]"
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
            className="resize-none rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] px-3 py-2 text-sm text-[hsl(var(--foreground))] placeholder:text-[hsl(var(--muted-foreground))] focus:outline-none focus:ring-2 focus:ring-[hsl(var(--ring))]"
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
              ? ` and visible to members of ${workspaceName ?? "the selected partner workspace"}`
              : ""}
            . It does not appear on the public map unless a reviewer approves it, and once
            approved its location becomes publicly visible.
          </span>
        </label>

        {error && (
          <p role="alert" className="text-sm text-red-600">
            {error}
          </p>
        )}

        <div className="flex justify-end gap-3 pt-1">
          <button
            type="submit"
            disabled={!canSubmit || !publicationConsent}
            className="min-h-11 rounded-lg bg-[hsl(var(--primary))] px-4 py-2 text-sm font-medium text-[hsl(var(--primary-foreground))] transition-opacity hover:opacity-90 disabled:opacity-50"
          >
            {submitMutation.isPending ? "Submitting..." : "Submit Recommendation"}
          </button>
        </div>
      </form>
    </div>
  );
}
