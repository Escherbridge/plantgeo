"use client";

import { useState } from "react";
import { useSession } from "next-auth/react";
import { trpc } from "@/lib/trpc/client";
import { SOCIAL_SIGN_IN_HREF } from "@/components/intervention/sign-in-gate";

/** One feature's like count and the viewer's own like state. */
export interface InterventionLikeButtonProps {
  /** A `geo.features.id`; the only key `trpc.interventionSocial.*` needs. */
  featureId: string;
  className?: string;
}

/**
 * The shared like control for a feature (FR-4), mounted on the map detail modal
 * and on every `/feed` row, so the two surfaces cannot drift.
 *
 * Update strategy: REFRESH-FROM-SERVER-RESPONSE, not optimistic. `toggleLike`
 * already returns the authoritative `{ liked, count }`, so the click applies
 * that response to local state -- no invented count that a failed mutation
 * would have to roll back, and no refetch round trip either. The control is one
 * persistent DOM node across the transition (no remount).
 *
 * `getLikeState` and `toggleLike` are both signed-in-only procedures, so a
 * signed-out viewer gets `/feed`'s sign-in treatment (an inert control plus a
 * login link) and the query is never issued.
 */
export function InterventionLikeButton({
  featureId,
  className,
}: InterventionLikeButtonProps) {
  const { status } = useSession();
  const authenticated = status === "authenticated";

  const likeStateQuery = trpc.interventionSocial.getLikeState.useQuery(
    { featureId },
    { enabled: authenticated, retry: false }
  );

  /** Null until the viewer acts; then it is the server's own last answer. */
  const [applied, setApplied] = useState<{ liked: boolean; count: number } | null>(
    null
  );

  const toggle = trpc.interventionSocial.toggleLike.useMutation({
    onSuccess: (data) => {
      if (data) setApplied({ liked: data.liked, count: data.count });
    },
  });

  const state = applied ?? likeStateQuery.data ?? { liked: false, count: 0 };
  const disabled = !authenticated || toggle.isPending;

  return (
    <span className={`inline-flex items-center gap-2 ${className ?? ""}`}>
      <button
        type="button"
        data-testid="intervention-like-button"
        data-liked={state.liked}
        aria-pressed={state.liked}
        aria-label={state.liked ? "Remove your like" : "Like this intervention"}
        disabled={disabled}
        onClick={() => {
          if (disabled) return;
          toggle.mutate({ featureId });
        }}
        className={`inline-flex items-center gap-1 rounded border px-2 py-1 text-xs ${
          state.liked
            ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-600"
            : "border-[hsl(var(--border))]"
        } ${disabled ? "opacity-60" : ""}`}
      >
        <span aria-hidden="true">♥</span>
        <span>Like</span>
        <span data-testid="intervention-like-count">{state.count}</span>
      </button>
      {authenticated ? null : (
        <a
          data-testid="intervention-like-signin"
          href={SOCIAL_SIGN_IN_HREF}
          className="text-xs underline"
        >
          Sign in to like
        </a>
      )}
    </span>
  );
}
