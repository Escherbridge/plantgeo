"use client";

import { useState } from "react";
import { trpc } from "@/lib/trpc/client";

export function ContributionQueue() {
  const [rejectNote, setRejectNote] = useState<Record<string, string>>({});
  const utils = trpc.useUtils();

  const { data: pending, isLoading } = trpc.contributions.listPendingReview.useQuery();

  const publishMutation = trpc.contributions.publishContribution.useMutation({
    onSuccess: () => utils.contributions.listPendingReview.invalidate(),
  });

  const rejectMutation = trpc.contributions.rejectContribution.useMutation({
    onSuccess: () => utils.contributions.listPendingReview.invalidate(),
  });

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
      {pending.map((feature) => (
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
            placeholder="Rejection note (optional)"
            value={rejectNote[feature.id] ?? ""}
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
                  reviewNote: rejectNote[feature.id],
                })
              }
              disabled={rejectMutation.isPending}
              className="flex-1 rounded bg-red-700 hover:bg-red-600 disabled:opacity-50 px-2 py-1 text-xs font-medium text-white transition-colors"
            >
              Reject
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}
