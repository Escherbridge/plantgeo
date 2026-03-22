"use client";

import { useState } from "react";
import { trpc } from "@/lib/trpc/client";
import { useAuthStore } from "@/stores/auth-store";

export function TeamSwitcher() {
  const { activeTeamId, setActiveTeam } = useAuthStore();
  const [open, setOpen] = useState(false);
  const { data: myTeams } = trpc.teams.listMyTeams.useQuery();

  const activeTeam = myTeams?.find((t) => t.team.id === activeTeamId);

  return (
    <div className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-2 rounded-md px-3 py-1.5 text-sm bg-zinc-800 hover:bg-zinc-700 text-zinc-100 transition-colors"
      >
        <span>{activeTeam?.team.name ?? "Personal"}</span>
        <svg
          className="h-4 w-4 text-zinc-400"
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {open && (
        <div className="absolute left-0 mt-1 w-52 rounded-md bg-zinc-900 border border-zinc-700 shadow-xl z-50">
          <button
            onClick={() => {
              setActiveTeam(null);
              setOpen(false);
            }}
            className={`w-full text-left px-3 py-2 text-sm hover:bg-zinc-800 transition-colors ${
              activeTeamId === null ? "text-emerald-400" : "text-zinc-200"
            }`}
          >
            Personal
          </button>
          {myTeams?.map(({ team, role }) => (
            <button
              key={team.id}
              onClick={() => {
                setActiveTeam(team.id);
                setOpen(false);
              }}
              className={`w-full text-left px-3 py-2 text-sm hover:bg-zinc-800 transition-colors ${
                activeTeamId === team.id ? "text-emerald-400" : "text-zinc-200"
              }`}
            >
              <span className="block">{team.name}</span>
              <span className="block text-xs text-zinc-500 capitalize">{role}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
