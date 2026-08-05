"use client";

import { useState } from "react";
import { trpc } from "@/lib/trpc/client";

/** A rejection with no reason can't be acted on by the submitter — see src/app/moderation/page.tsx. */
function hasRejectReason(note: string): boolean {
  return note.trim().length > 0;
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
        return (
          <div
            key={feature.id}
            className="rounded-md border border-zinc-700 bg-zinc-800 p-3 flex flex-col gap-2"
          >
            <div className="text-xs text-zinc-400 font-mono">{feature.id}</div>
            <div className="text-xs text-zinc-300">
              Layer: {feature.layerId}
            </div>
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
