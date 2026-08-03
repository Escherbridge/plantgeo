"use client";

import { useSession } from "next-auth/react";
import { trpc } from "@/lib/trpc/client";

/**
 * Resolves the caller's active organization + their role in it from
 * `listMyTeams`, preferring the session's `activeTeamId` and falling back to
 * the first membership while a fresh session hasn't propagated yet.
 */
export function useActiveOrganization() {
  const { data: session } = useSession();
  const sessionActiveTeamId = session?.user?.activeTeamId ?? null;

  const query = trpc.teams.listMyTeams.useQuery();
  const memberships = query.data;

  const active =
    (sessionActiveTeamId
      ? memberships?.find((entry) => entry.team.id === sessionActiveTeamId)
      : undefined) ?? memberships?.[0];

  return {
    organization: active?.team,
    role: active?.role,
    memberships,
    isLoading: query.isLoading,
    isError: query.isError,
    error: query.error,
    refetch: query.refetch,
  };
}
