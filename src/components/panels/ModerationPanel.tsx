"use client";

import React, { useState } from "react";
import { trpc } from "@/lib/trpc/client";
import { ShieldCheck, CheckCircle2, XCircle, AlertCircle, Info, Sparkles, MapPin } from "lucide-react";

/** Effect evidence for one proposal; only the absence case exists today. See AGENTS.md. */
type EffectEvidence = { kind: "unavailable"; reason: "no_evaluated_estimate" };

/** `listProposed` returns no effect estimate, so every proposal reports the absence. */
const NO_EVALUATED_ESTIMATE: EffectEvidence = {
  kind: "unavailable",
  reason: "no_evaluated_estimate",
};

/** Renders effect evidence; the absence is stated, never stood in for by a number. */
function EffectEvidenceNotice({ evidence }: { evidence: EffectEvidence }) {
  if (evidence.kind === "unavailable") {
    return (
      <div className="bg-zinc-900 border border-zinc-800 px-4 py-2 rounded-lg flex items-start gap-3 max-w-sm">
        <Info className="w-4 h-4 text-zinc-500 shrink-0 mt-0.5" />
        <div>
          <div className="text-xs text-zinc-300">
            No evaluated effect estimate is available for this proposal
          </div>
          <div className="text-[11px] text-zinc-500 mt-0.5">
            An estimate may appear here once a provenance-carrying, time-honest evaluation has produced one.
          </div>
        </div>
      </div>
    );
  }
  return null;
}

export function ModerationPanel() {
  const [selectedItem, setSelectedItem] = useState<string | null>(null);
  const [reviewNote, setReviewNote] = useState("");
  const utils = trpc.useUtils();

  const { data: proposed, isLoading } = trpc.interventions.listProposed.useQuery({});

  const castVote = trpc.interventions.castModerationVote.useMutation({
    onSuccess: () => {
      setReviewNote("");
      setSelectedItem(null);
      utils.interventions.listProposed.invalidate();
    },
  });

  const transitionState = trpc.interventions.transitionLifecycleState.useMutation({
    onSuccess: () => {
      utils.interventions.listProposed.invalidate();
    },
  });

  const handleVote = (interventionId: string, vote: "approve" | "reject" | "request_revision") => {
    castVote.mutate({
      interventionId,
      vote,
      note: reviewNote || undefined,
    });
  };

  const handleTransition = (interventionId: string, targetState: "proposed" | "approved" | "active" | "monitored") => {
    transitionState.mutate({
      interventionId,
      targetState,
    });
  };

  if (isLoading) {
    return (
      <div className="p-8 text-center text-zinc-400">
        <Sparkles className="w-6 h-6 animate-pulse mx-auto mb-2 text-emerald-400" />
        <span>Loading Expert Moderation Queue...</span>
      </div>
    );
  }

  return (
    <div className="p-6 space-y-6 text-zinc-100">
      <div className="flex items-center justify-between border-b border-zinc-800 pb-4">
        <div>
          <h2 className="text-lg font-bold text-white flex items-center gap-2">
            <ShieldCheck className="w-5 h-5 text-emerald-400" />
            Expert Moderation & Lifecycle Queue
          </h2>
          <p className="text-xs text-zinc-400 mt-1">
            Review submitted proposals and cast verified moderation votes
          </p>
        </div>
        <div className="px-3 py-1 bg-emerald-500/10 border border-emerald-500/20 text-emerald-300 text-xs font-mono rounded-full">
          {proposed?.length || 0} Pending Items
        </div>
      </div>

      {proposed?.length === 0 ? (
        <div className="p-12 text-center border border-dashed border-zinc-800 rounded-xl text-zinc-500">
          No proposals awaiting expert moderation review.
        </div>
      ) : (
        <div className="space-y-4">
          {proposed?.map((item) => {
            const isSelected = selectedItem === item.id;

            return (
              <div
                key={item.id}
                className="bg-zinc-900/90 border border-zinc-800 hover:border-zinc-700 p-5 rounded-xl transition shadow-md"
              >
                <div className="flex flex-col md:flex-row md:items-center justify-between gap-3">
                  <div>
                    <div className="flex items-center space-x-3">
                      <span className="font-semibold text-white text-base">
                        {item.name || "Untitled Proposal"}
                      </span>
                      <span className="text-xs font-mono uppercase px-2 py-0.5 rounded bg-zinc-800 text-emerald-300 border border-zinc-700">
                        {item.type || "Intervention"}
                      </span>
                    </div>

                    <div className="flex items-center space-x-4 text-xs text-zinc-400 mt-2">
                      <span className="flex items-center gap-1">
                        <MapPin className="w-3.5 h-3.5 text-zinc-500" />
                        {item.latitude?.toFixed(4)}, {item.longitude?.toFixed(4)}
                      </span>
                      <span>Submitted: {item.createdAt ? new Date(item.createdAt).toLocaleDateString() : "Recently"}</span>
                    </div>
                  </div>

                  <EffectEvidenceNotice evidence={NO_EVALUATED_ESTIMATE} />
                </div>

                {item.description && (
                  <p className="text-xs text-zinc-300 mt-3 bg-zinc-950/60 p-3 rounded border border-zinc-800/80">
                    {item.description}
                  </p>
                )}

                {/* Moderation Controls */}
                <div className="mt-4 pt-3 border-t border-zinc-800 flex flex-col md:flex-row md:items-center justify-between gap-3">
                  <div className="flex items-center space-x-2">
                    <button
                      onClick={() => handleVote(item.id, "approve")}
                      className="flex items-center space-x-1.5 px-3 py-1.5 bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold rounded-lg transition"
                    >
                      <CheckCircle2 className="w-4 h-4" />
                      <span>Approve & Publish</span>
                    </button>

                    <button
                      onClick={() => handleVote(item.id, "request_revision")}
                      className="flex items-center space-x-1.5 px-3 py-1.5 bg-amber-600 hover:bg-amber-500 text-white text-xs font-semibold rounded-lg transition"
                    >
                      <AlertCircle className="w-4 h-4" />
                      <span>Request Revision</span>
                    </button>

                    <button
                      onClick={() => handleVote(item.id, "reject")}
                      className="flex items-center space-x-1.5 px-3 py-1.5 bg-rose-600 hover:bg-rose-500 text-white text-xs font-semibold rounded-lg transition"
                    >
                      <XCircle className="w-4 h-4" />
                      <span>Reject</span>
                    </button>
                  </div>

                  <div className="flex items-center space-x-2">
                    <span className="text-[11px] text-zinc-400">Lifecycle:</span>
                    <button
                      onClick={() => handleTransition(item.id, "active")}
                      className="px-2.5 py-1 bg-zinc-800 hover:bg-zinc-700 text-cyan-300 text-xs font-mono rounded border border-zinc-700"
                    >
                      Set ACTIVE
                    </button>
                    <button
                      onClick={() => handleTransition(item.id, "monitored")}
                      className="px-2.5 py-1 bg-zinc-800 hover:bg-zinc-700 text-purple-300 text-xs font-mono rounded border border-zinc-700"
                    >
                      Set MONITORED
                    </button>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
