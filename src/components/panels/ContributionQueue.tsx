"use client";

import Link from "next/link";
import { useState } from "react";
import { buildMapFocusHref } from "@/lib/map/focus-params";
import { trpc } from "@/lib/trpc/client";

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
  const utils = trpc.useUtils();

  const { data: pending, isLoading, error } = trpc.contributions.listPendingReview.useQuery();

  const publishMutation = trpc.contributions.publishContribution.useMutation({
    onSuccess: () => utils.contributions.listPendingReview.invalidate(),
  });

  const rejectMutation = trpc.contributions.rejectContribution.useMutation({
    onSuccess: (_data, variables) => {
      utils.contributions.listPendingReview.invalidate();
      setRejectNote((n) => {
        const { [variables.featureId]: _removed, ...rest } = n;
        return rest;
      });
    },
  });

  // The server-side gate (expertProcedure) is authoritative; this only keeps a
  // stale client from rendering a raw error instead of a plain message.
  if (error) {
    return (
      <div className="p-4 text-sm text-zinc-400">
        You do not have access to this queue.
      </div>
    );
  }

  if (isLoading) {
    return <div className="p-4 text-sm text-zinc-400">Loading...</div>;
  }

  if (!pending?.length) {
    return (
      <div className="p-4 text-sm text-zinc-400">No pending contributions.</div>
    );
  }

  return (
    <div className="flex flex-col gap-3 p-4">
      <h2 className="text-sm font-semibold text-zinc-100">
        Pending Review ({pending.length})
      </h2>
      {pending.map((feature) => {
        const note = rejectNote[feature.id] ?? "";
        const canReject = hasRejectReason(note);
        const summary = readSubmissionSummary(feature.properties);
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

            {/* Approving publishes a location to the public map, so the reviewer gets to
                look at it first — same camera deep-link contract /feed writes. */}
            {summary.longitude !== null && summary.latitude !== null ? (
              focusHref ? (
                <Link
                  href={focusHref}
                  className="text-xs font-mono text-emerald-400 hover:text-emerald-300 hover:underline w-fit"
                >
                  {formatCoordinates(summary.longitude, summary.latitude)} — view on map
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
              placeholder="Rejection note (required to reject)"
              value={note}
              onChange={(e) =>
                setRejectNote((n) => ({ ...n, [feature.id]: e.target.value }))
              }
              className="rounded bg-zinc-900 border border-zinc-700 px-2 py-1 text-xs text-zinc-100 placeholder-zinc-600 focus:outline-none focus:ring-1 focus:ring-emerald-500"
            />
            <div className="flex gap-2">
              <button
                onClick={() =>
                  publishMutation.mutate({ featureId: feature.id })
                }
                disabled={publishMutation.isPending}
                className="flex-1 rounded bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 px-2 py-1 text-xs font-medium text-white transition-colors"
              >
                Approve
              </button>
              <button
                onClick={() =>
                  rejectMutation.mutate({
                    featureId: feature.id,
                    reviewNote: note.trim(),
                  })
                }
                disabled={rejectMutation.isPending || !canReject}
                title={canReject ? undefined : "A rejection note is required"}
                className="flex-1 rounded bg-red-700 hover:bg-red-600 disabled:opacity-50 disabled:cursor-not-allowed px-2 py-1 text-xs font-medium text-white transition-colors"
              >
                Reject
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );
}
