"use client";

import { useState } from "react";
import { trpc } from "@/lib/trpc/client";
import { TOUCH_TARGET } from "@/components/ui/touch-target";

/** Post-publication lifecycle manager for /moderation; see src/components/panels/AGENTS.md §moderation. */

const STAGE_LABEL: Record<string, string> = {
  published: "Published",
  active: "Active",
  monitored: "Monitored",
};

const ADVANCE_LABEL: Record<string, string> = {
  active: "Mark active",
  monitored: "Mark monitored",
};

function stageLabel(stage: string): string {
  return STAGE_LABEL[stage] ?? stage;
}

export function ModerationPanel() {
  const [transitionFailure, setTransitionFailure] = useState<string | null>(null);
  const utils = trpc.useUtils();

  const { data: interventions, isLoading, error } = trpc.interventions.listLifecycle.useQuery({});

  const transition = trpc.interventions.transitionLifecycleState.useMutation({
    onSuccess: () => {
      setTransitionFailure(null);
      return utils.interventions.listLifecycle.invalidate();
    },
    onError: (failure: { data?: { code?: string } | null }) => {
      setTransitionFailure(
        failure.data?.code === "CONFLICT"
          ? "This intervention's stage changed before your update. Your change was not applied."
          : "Could not update this intervention's stage. Refresh the list before trying again."
      );
      void utils.interventions.listLifecycle.invalidate();
    },
  });

  const failureAlert = transitionFailure ? (
    <p role="alert" className="text-sm text-amber-300">{transitionFailure}</p>
  ) : null;

  if (error) {
    const accessDenied = error.data?.code === "FORBIDDEN" || error.data?.code === "UNAUTHORIZED";
    return (
      <div className="p-4 text-sm text-zinc-400">
        <p>{accessDenied
          ? "You do not have access to intervention lifecycle management."
          : "Could not load published interventions. Refresh the page to try again."}</p>
      </div>
    );
  }

  if (isLoading) {
    return <div className="p-4 text-sm text-zinc-400">Loading...</div>;
  }

  if (!interventions?.length) {
    return (
      <div className="p-4 text-sm text-zinc-400">
        {failureAlert}
        <p>No published interventions yet.</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3 p-4">
      {failureAlert}
      <h2 className="text-sm font-semibold text-zinc-100">
        Published interventions ({interventions.length})
      </h2>
      {interventions.map((item) => (
        <div
          key={item.id}
          title={`Feature ${item.id}`}
          className="rounded-md border border-zinc-700 bg-zinc-800 p-3 flex flex-col gap-2"
        >
          <div className="flex items-center justify-between gap-2">
            <p className="text-sm font-medium text-zinc-100">{item.name ?? "Untitled intervention"}</p>
            <span className="rounded border border-zinc-600 px-2 py-0.5 text-xs font-mono text-zinc-300">
              {stageLabel(item.stage)}
            </span>
          </div>
          {item.type && <p className="text-xs text-zinc-400">{item.type.replace(/[_-]+/g, " ")}</p>}
          {item.fromStage && item.nextStages.length > 0 ? (
            <div className="flex gap-2">
              {item.nextStages.map((toStage) => (
                <button
                  key={toStage}
                  onClick={() =>
                    // `fromStage` is what this reader saw; the server refuses with CONFLICT if it moved.
                    item.fromStage && transition.mutate({ interventionId: item.id, fromStage: item.fromStage, toStage })
                  }
                  disabled={transition.isPending}
                  className={`flex-1 rounded bg-cyan-700 hover:bg-cyan-600 disabled:opacity-50 px-2 py-1 text-xs font-medium text-white transition-colors ${TOUCH_TARGET}`}
                >
                  {ADVANCE_LABEL[toStage] ?? `Mark ${toStage}`}
                </button>
              ))}
            </div>
          ) : (
            <p className="text-xs text-zinc-500">No further lifecycle stage.</p>
          )}
        </div>
      ))}
    </div>
  );
}
