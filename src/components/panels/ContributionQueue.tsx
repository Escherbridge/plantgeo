"use client";

import Link from "next/link";
import { useState } from "react";
import { buildMapFocusHref } from "@/lib/map/focus-params";
import { trpc } from "@/lib/trpc/client";
import { notifyInterventionPublication } from "@/lib/map/intervention-publication";
import { InterventionGeometryPreview } from "./InterventionGeometryPreview";

/** A rejection with no reason can't be acted on by the submitter — see src/app/moderation/page.tsx. */
function hasRejectReason(note: string): boolean {
  return note.trim().length > 0;
}

/**
 * What a reviewer needs to see, read out of the untyped `properties` bag.
 *
 * Nothing here is trusted: `properties` is jsonb, and while
 * `interventions.submitIntervention` writes a known shape, the machine-ingress route at
 * `src/app/api/ingest/interventions/route.ts` and any future writer share this column and
 * this queue filters on status alone, not on layer. Every field narrows or falls away.
 */
interface SubmissionSummary {
  name: string | null;
  category: string | null;
  description: string | null;
  longitude: number | null;
  latitude: number | null;
}

/** The first coordinate position of any GeoJSON geometry — enough to fly the camera there. */
function firstPosition(coordinates: unknown): [number, number] | null {
  if (!Array.isArray(coordinates)) return null;
  const [first, second] = coordinates;
  if (typeof first === "number" && typeof second === "number") {
    return [first, second];
  }
  return firstPosition(first);
}

function readSubmissionSummary(properties: unknown): SubmissionSummary {
  const bag = (properties ?? {}) as Record<string, unknown>;
  const geometry = (bag.geometry ?? {}) as { coordinates?: unknown };
  const position = firstPosition(geometry.coordinates);
  return {
    name: typeof bag.name === "string" && bag.name.trim() !== "" ? bag.name : null,
    category: typeof bag.type === "string" && bag.type.trim() !== "" ? bag.type : null,
    description:
      typeof bag.description === "string" && bag.description.trim() !== ""
        ? bag.description
        : null,
    longitude: position?.[0] ?? null,
    latitude: position?.[1] ?? null,
  };
}

/**
 * "cover_cropping" → "Cover cropping". Deliberately a transform rather than a fourth copy of
 * the intervention label table: this queue serves whatever layer has rows in review, so it
 * cannot assume the intervention vocabulary.
 */
function humanizeCategory(category: string): string {
  const spaced = category.replace(/[_-]+/g, " ").trim();
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

/** Four decimals is ~11 m — enough to place a site, short enough to read in a queue row. */
function formatCoordinates(longitude: number, latitude: number): string {
  return `${latitude.toFixed(4)}, ${longitude.toFixed(4)}`;
}

export function ContributionQueue() {
  const [rejectNote, setRejectNote] = useState<Record<string, string>>({});
  const [actionError, setActionError] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<string | null>(null);
  const utils = trpc.useUtils();

  const { data: pending, isLoading, error, refetch } = trpc.contributions.listPendingReview.useQuery();

  const invalidateCommunity = async () => {
    await Promise.all([
      utils.contributions.listPendingReview.invalidate(),
      utils.interventions.listMySubmissions.invalidate(),
      utils.interventions.listProposed.invalidate(),
      utils.wildfire.getInterventions.invalidate(),
    ]);
  };

  const handleReviewError = async (
    error: { message: string; data?: { code?: string } | null },
    variables: { featureId: string },
  ) => {
    setOutcome(null);
    setActionError(error.message);
    if (error.data?.code === "CONFLICT") {
      setRejectNote((notes) => {
        const { [variables.featureId]: _removed, ...rest } = notes;
        return rest;
      });
      await refetch();
    }
  };

  const publishMutation = trpc.contributions.publishContribution.useMutation({
    onSuccess: async () => {
      setActionError(null);
      setOutcome("Published to the public map.");
      await notifyInterventionPublication();
      await invalidateCommunity();
    },
    onError: handleReviewError,
  });

  const rejectMutation = trpc.contributions.rejectContribution.useMutation({
    onSuccess: (_data, variables) => {
      setActionError(null);
      setOutcome("Submission rejected. The contributor can read your note and revise it.");
      void invalidateCommunity();
      setRejectNote((n) => {
        const { [variables.featureId]: _removed, ...rest } = n;
        return rest;
      });
    },
    onError: handleReviewError,
  });

  const revisionMutation = trpc.contributions.requestRevisionContribution.useMutation({
    onSuccess: () => {
      setActionError(null);
      setOutcome("Revision requested. The contributor can read your note and resubmit.");
      void invalidateCommunity();
    },
    onError: handleReviewError,
  });
  const busy = publishMutation.isPending || rejectMutation.isPending || revisionMutation.isPending;

  // The server-side gate (expertProcedure) is authoritative; this only keeps a
  // stale client from rendering a raw error instead of a plain message.
  if (error) {
    const code = error.data?.code;
    const message = code === "UNAUTHORIZED" ? "Sign in to review contributions."
      : code === "FORBIDDEN" ? "Only experts and administrators can review contributions."
      : code === "PRECONDITION_FAILED" ? "Community review is not provisioned in this environment."
      : "The review service is unavailable. Your queue could not be loaded.";
    return (
      <div role="alert" className="p-4 text-sm text-zinc-400">
        {message}
        <button onClick={() => void refetch()} className="ml-3 underline">Retry</button>
      </div>
    );
  }

  if (isLoading || !pending) {
    return <div className="p-4 text-sm text-zinc-400">Loading...</div>;
  }

  if (!pending?.length) {
    return (
      <div className="p-4 text-sm text-zinc-400">
        {actionError && <p role="alert">{actionError}</p>}
        {outcome && <p role="status">{outcome}</p>}
        No pending contributions or eligible legacy approvals awaiting review.
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3 p-4">
      <h2 className="text-sm font-semibold text-zinc-100">
        Awaiting Review ({pending.length})
      </h2>
      {actionError && <p role="alert" className="text-sm text-rose-300">{actionError}</p>}
      {outcome && <p role="status" className="text-sm text-emerald-300">{outcome}</p>}
      {pending.map((feature) => {
        const note = rejectNote[feature.id] ?? "";
        const canReject = hasRejectReason(note);
        const summary = readSubmissionSummary(feature.properties);
        const recovery = feature.status === "approved";
        const focusHref =
          summary.longitude !== null && summary.latitude !== null
            ? buildMapFocusHref(summary.longitude, summary.latitude)
            : null;
        return (
          <div
            key={feature.id}
            // The row shows what is being approved; the id stays available for support
            // lookups without spending a line of the card on a UUID nobody reads.
            title={`Feature ${feature.id}`}
            className="rounded-md border border-zinc-700 bg-zinc-800 p-3 flex flex-col gap-2"
          >
            <div className="flex flex-col gap-1">
              <p className="text-sm font-medium text-zinc-100">
                {summary.name ?? "Untitled submission"}
              </p>
              <p className="text-xs text-zinc-400">
                {summary.category ? `${humanizeCategory(summary.category)} · ` : ""}
                {feature.layerName}
                {feature.createdAt && (
                  <> · submitted {new Date(feature.createdAt).toLocaleDateString()}</>
                )}
              </p>
            </div>

            {summary.description && (
              <p className="text-xs text-zinc-300 line-clamp-3">{summary.description}</p>
            )}
            {recovery && (
              <p className="text-xs text-amber-300">Legacy approval is not public. Review this site individually and record why publication is appropriate before recovering it.</p>
            )}
            <p className="text-xs text-zinc-400">No evaluated effect estimate is available for this proposal.</p>
            {feature.layerName === "interventions" && (
              <InterventionGeometryPreview geometry={(feature.properties as Record<string, unknown> | null)?.geometry} />
            )}

            {/* Approving publishes a location to the public map, so the reviewer gets to
                look at it first — same camera deep-link contract /feed writes. */}
            {summary.longitude !== null && summary.latitude !== null ? (
              focusHref ? (
                <Link
                  href={focusHref}
                  className="text-xs font-mono text-emerald-400 hover:text-emerald-300 hover:underline w-fit"
                >
                  {formatCoordinates(summary.longitude, summary.latitude)} — view location on map
                </Link>
              ) : (
                <p className="text-xs font-mono text-amber-400">
                  Coordinates out of range — inspect before publishing
                </p>
              )
            ) : (
              <p className="text-xs text-zinc-500">No location on this submission</p>
            )}

            <input
              type="text"
              aria-label={`Review note for ${summary.name ?? "Untitled submission"}`}
              maxLength={2000}
              placeholder={recovery ? "Recovery review note (required to publish)" : "Review note (required to reject or request revision)"}
              value={note}
              onChange={(e) =>
                setRejectNote((n) => ({ ...n, [feature.id]: e.target.value }))
              }
              className="rounded bg-zinc-900 border border-zinc-700 px-2 py-1 text-xs text-zinc-100 placeholder-zinc-600 focus:outline-none focus:ring-1 focus:ring-emerald-500"
            />
            <div className="flex gap-2">
              <button
                onClick={() =>
                  publishMutation.mutate({ featureId: feature.id, expectedReviewVersion: feature.reviewVersion, ...(recovery ? { recoveryReviewNote: note.trim() } : {}) })
                }
                disabled={busy || (recovery && !canReject)}
                className="flex-1 rounded bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 px-2 py-1 text-xs font-medium text-white transition-colors"
              >
                {recovery ? "Publish reviewed legacy approval" : "Approve & Publish"}
              </button>
              {!recovery && feature.layerName === "interventions" && <button
                onClick={() => revisionMutation.mutate({ featureId: feature.id, expectedReviewVersion: feature.reviewVersion, reviewNote: note.trim() })}
                disabled={busy || !canReject}
                className="flex-1 rounded bg-amber-700 px-2 py-1 text-xs text-white disabled:opacity-50"
              >Request revision</button>}
              {!recovery && (
              <button
                onClick={() =>
                  rejectMutation.mutate({
                    featureId: feature.id,
                    expectedReviewVersion: feature.reviewVersion,
                    reviewNote: note.trim(),
                  })
                }
                disabled={busy || !canReject}
                title={canReject ? undefined : "A rejection note is required"}
                className="flex-1 rounded bg-red-700 hover:bg-red-600 disabled:opacity-50 disabled:cursor-not-allowed px-2 py-1 text-xs font-medium text-white transition-colors"
              >
                Reject
              </button>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
