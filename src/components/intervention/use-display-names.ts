"use client";

import { useMemo } from "react";
import { trpc } from "@/lib/trpc/client";

/**
 * The one client-side display-name resolver (Phase 4 / FR-4, OQ-E).
 *
 * Every surface that identifies a contributor -- the comment thread, the map
 * detail panel -- reads through this hook so the fallback string exists in
 * exactly one place (`contributorFallbackLabel`, below). The server
 * deliberately returns `name: null` rather than a baked fallback, per
 * `users.getDisplayNames`.
 *
 * Batching: the ids are de-duplicated and sorted into a stable query key, so a
 * page of comments by three authors issues ONE `getDisplayNames` call, not one
 * per comment, and a re-render with the same author set reuses it.
 */

export interface DisplayNameDirectory {
  /** The resolved `users.name`, or null when unset/unknown/not yet loaded. */
  nameFor: (userId: string) => string | null;
  /** The resolved avatar url, or null. */
  imageFor: (userId: string) => string | null;
  /** `users.name` when set, else the stable id fragment. */
  labelFor: (userId: string) => string;
}

/** The pre-directory fallback this codebase already showed, kept verbatim. */
export function contributorFallbackLabel(userId: string): string {
  return `Contributor ${userId.slice(0, 8)}`;
}

/** Matches `MAX_DISPLAY_NAME_IDS` on the procedure; a longer list is truncated, not rejected. */
const MAX_BATCH = 100;

export function useDisplayNames(userIds: readonly string[]): DisplayNameDirectory {
  // Sorted + de-duplicated: the query key is the author SET, so re-ordering a
  // list (or re-rendering it) cannot issue a second request for the same people.
  const batch = useMemo(() => {
    return Array.from(new Set(userIds.filter(Boolean))).sort().slice(0, MAX_BATCH);
  }, [userIds]);

  const query = trpc.users.getDisplayNames.useQuery(
    { userIds: batch },
    { enabled: batch.length > 0, retry: false, staleTime: 5 * 60 * 1000 }
  );

  const resolved = useMemo(() => {
    const rows = (query.data ?? []) as {
      id: string;
      name: string | null;
      image: string | null;
    }[];
    return new Map(rows.map((row) => [row.id, row]));
  }, [query.data]);

  return useMemo<DisplayNameDirectory>(
    () => ({
      nameFor: (userId) => resolved.get(userId)?.name ?? null,
      imageFor: (userId) => resolved.get(userId)?.image ?? null,
      labelFor: (userId) =>
        resolved.get(userId)?.name ?? contributorFallbackLabel(userId),
    }),
    [resolved]
  );
}
