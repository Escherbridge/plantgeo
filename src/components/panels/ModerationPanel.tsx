"use client";

import { useEffect, useState } from "react";
import type { inferRouterOutputs } from "@trpc/server";
import { trpc } from "@/lib/trpc/client";
import type { AppRouter } from "@/lib/trpc/client";
import { TOUCH_TARGET } from "@/components/ui/touch-target";

/** Post-publication lifecycle manager for /moderation; see src/components/panels/AGENTS.md §moderation. */

type RouterOutputs = inferRouterOutputs<AppRouter>;
type LifecycleItem = RouterOutputs["interventions"]["listLifecycle"]["items"][number];
type LifecycleCursor = NonNullable<RouterOutputs["interventions"]["listLifecycle"]["nextCursor"]>;

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
  // `cursor` names the page currently being fetched; `items` is every page fetched so far,
  // concatenated. They are separate because `listLifecycle` answers one page per call -- see
  // src/components/panels/AGENTS.md §moderation -- so accumulation has to happen here rather
  // than inside a single query's cache entry (no `useInfiniteQuery` elsewhere in this codebase).
  const [cursor, setCursor] = useState<LifecycleCursor | null>(null);
  const [items, setItems] = useState<LifecycleItem[]>([]);
  const utils = trpc.useUtils();

  const { data, isLoading, isFetching, error } = trpc.interventions.listLifecycle.useQuery({ cursor });

  useEffect(() => {
    if (!data) return;
    // `cursor === null` is the first page of a (re)load -- replace rather than append, so an
    // invalidated first page cannot be concatenated onto itself.
    // A refetch of a later page re-delivers rows already held: keep the first copy of each id.
    setItems((previous) => {
      if (cursor === null) return data.items;
      const held = new Set(previous.map((item) => item.id));
      return [...previous, ...data.items.filter((item) => !held.has(item.id))];
    });
  }, [data, cursor]);

  function resetToFirstPage() {
    setCursor(null);
    setItems([]);
  }

  const transition = trpc.interventions.transitionLifecycleState.useMutation({
    onSuccess: () => {
      setTransitionFailure(null);
      // A stage move can change this row's position in the `updatedAt DESC` order, which
      // invalidates every page after the first -- simplest correct move is to drop the
      // accumulated pages and let the first page refetch.
      resetToFirstPage();
      return utils.interventions.listLifecycle.invalidate();
    },
    onError: (failure: { data?: { code?: string } | null }) => {
      setTransitionFailure(
        failure.data?.code === "CONFLICT"
          ? "This intervention's stage changed before your update. Your change was not applied."
          : "Could not update this intervention's stage. Refresh the list before trying again."
      );
      resetToFirstPage();
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

  if (isLoading && items.length === 0) {
    return <div className="p-4 text-sm text-zinc-400">Loading...</div>;
  }

  if (items.length === 0) {
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
        Published interventions ({items.length}
        {data?.nextCursor ? "+" : ""})
      </h2>
      {items.map((item) => (
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
      {data?.nextCursor && (
        <button
          type="button"
          onClick={() => data.nextCursor && setCursor(data.nextCursor)}
          disabled={isFetching}
          className={`self-start rounded border border-zinc-600 px-3 py-1.5 text-xs font-medium text-zinc-200 hover:bg-zinc-800 disabled:opacity-50 ${TOUCH_TARGET}`}
        >
          {isFetching ? "Loading..." : "Load more"}
        </button>
      )}
    </div>
  );
}
