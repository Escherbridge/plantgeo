"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useSession } from "next-auth/react";
import { Check, ChevronDown, Loader2, Plus } from "lucide-react";
import { trpc } from "@/lib/trpc/client";
import { useAuthStore } from "@/stores/auth-store";
import { cn } from "@/lib/utils";

function initialsFor(name: string): string {
  const trimmed = name.trim();
  if (!trimmed) return "?";
  return trimmed
    .split(/\s+/)
    .slice(0, 2)
    .map((word) => word[0]?.toUpperCase())
    .join("");
}

/**
 * Organization switcher. Persists the choice server-side via `setActiveTeam`,
 * then refreshes the NextAuth JWT (`update`) so session-derived checks —
 * middleware included — see the new active organization immediately.
 */
export function TeamSwitcher() {
  const { data: session, update: updateSession } = useSession();
  const { activeTeamId: storeActiveTeamId, setActiveTeam: setStoreActiveTeam } = useAuthStore();
  const [open, setOpen] = useState(false);
  const utils = trpc.useUtils();

  const { data: myTeams, isLoading, isError, error } = trpc.teams.listMyTeams.useQuery();
  const setActiveMutation = trpc.teams.setActiveTeam.useMutation();

  const sessionActiveTeamId = session?.user?.activeTeamId ?? null;
  const activeTeamId = storeActiveTeamId ?? sessionActiveTeamId;

  // Keep the legacy Zustand store aligned with the session so panels that still
  // read from it (community, team detail) stay correct after a switch elsewhere.
  useEffect(() => {
    if (sessionActiveTeamId !== storeActiveTeamId) {
      setStoreActiveTeam(sessionActiveTeamId);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionActiveTeamId]);

  const activeTeam = myTeams?.find((entry) => entry.team.id === activeTeamId);
  const switching = setActiveMutation.isPending;

  async function switchTo(teamId: string) {
    if (teamId === activeTeamId || switching) return;
    try {
      await setActiveMutation.mutateAsync({ teamId });
      setStoreActiveTeam(teamId);
      await updateSession({ activeTeamId: teamId });
      await utils.teams.listMyTeams.invalidate();
    } finally {
      setOpen(false);
    }
  }

  return (
    <div className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        disabled={isLoading}
        className="flex items-center gap-2 rounded-md px-3 py-1.5 text-sm bg-zinc-800 hover:bg-zinc-700 text-zinc-100 transition-colors disabled:opacity-60"
      >
        {isLoading ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin text-zinc-400" />
        ) : (
          <span className="flex h-5 w-5 items-center justify-center rounded bg-emerald-600/20 text-[10px] font-semibold text-emerald-400">
            {activeTeam ? initialsFor(activeTeam.team.name) : "—"}
          </span>
        )}
        <span className="max-w-36 truncate">
          {isLoading ? "Loading…" : (activeTeam?.team.name ?? "No organization")}
        </span>
        <ChevronDown className="h-4 w-4 text-zinc-400" />
      </button>

      {open && (
        <div className="absolute left-0 mt-1 w-64 rounded-md bg-zinc-900 border border-zinc-700 shadow-xl z-50">
          {isError && (
            <p className="px-3 py-2 text-xs text-red-400">{error.message}</p>
          )}

          {!isError && myTeams?.length === 0 && (
            <p className="px-3 py-2 text-xs text-zinc-500">You don&rsquo;t belong to any organizations yet.</p>
          )}

          {myTeams?.map(({ team, role }) => (
            <button
              key={team.id}
              onClick={() => switchTo(team.id)}
              disabled={switching}
              className={cn(
                "flex w-full items-center gap-2 px-3 py-2 text-left text-sm transition-colors hover:bg-zinc-800 disabled:cursor-wait disabled:opacity-60",
                activeTeamId === team.id ? "text-emerald-400" : "text-zinc-200"
              )}
            >
              <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded bg-zinc-800 text-[10px] font-semibold text-zinc-300">
                {initialsFor(team.name)}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate">{team.name}</span>
                <span className="block text-xs text-zinc-500 capitalize">{role}</span>
              </span>
              {activeTeamId === team.id &&
                (switching ? (
                  <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin" />
                ) : (
                  <Check className="h-3.5 w-3.5 shrink-0" />
                ))}
            </button>
          ))}

          <div className="border-t border-zinc-800 p-1">
            <Link
              href="/onboarding?create=1"
              onClick={() => setOpen(false)}
              className="flex items-center gap-2 rounded px-2 py-1.5 text-sm text-emerald-400 transition-colors hover:bg-zinc-800"
            >
              <Plus className="h-3.5 w-3.5" />
              Create organization
            </Link>
          </div>
        </div>
      )}
    </div>
  );
}
