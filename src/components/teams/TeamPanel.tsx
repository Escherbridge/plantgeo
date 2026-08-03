"use client";

import { useState } from "react";
import { trpc } from "@/lib/trpc/client";
import { useAuthStore } from "@/stores/auth-store";

export function TeamPanel() {
  const { activeTeamId } = useAuthStore();
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] =
    useState<"member" | "viewer">("viewer");
  const utils = trpc.useUtils();

  const { data: myTeams } = trpc.teams.listMyTeams.useQuery();
  const activeTeam = myTeams?.find((entry) => entry.team.id === activeTeamId);

  const inviteMutation = trpc.teams.createInvitation.useMutation({
    onSuccess: () => {
      setInviteEmail("");
      utils.teams.listMyTeams.invalidate();
    },
  });

  if (!activeTeamId || !activeTeam) {
    return (
      <div className="p-4 text-sm text-zinc-400">
        Select a team to manage members.
      </div>
    );
  }

  const canInvite = activeTeam.role === "owner" || activeTeam.role === "member";

  return (
    <div className="flex flex-col gap-4 p-4">
      <div>
        <h2 className="text-base font-semibold text-zinc-100">
          {activeTeam.team.name}
        </h2>
        {activeTeam.team.description && (
          <p className="mt-1 text-sm text-zinc-400">
            {activeTeam.team.description}
          </p>
        )}
      </div>

      {canInvite && (
        <div>
          <h3 className="mb-2 text-sm font-medium text-zinc-300">
            Invite Member
          </h3>
          <div className="flex gap-2">
            <input
              type="email"
              placeholder="Email address"
              aria-label="Email address"
              value={inviteEmail}
              onChange={(event) => setInviteEmail(event.target.value)}
              className="flex-1 rounded-md border border-zinc-700 bg-zinc-800 px-3 py-1.5 text-sm text-zinc-100 placeholder-zinc-500 focus:outline-none focus:ring-1 focus:ring-emerald-500"
            />
            <select
              value={inviteRole}
              onChange={(event) =>
                setInviteRole(event.target.value as "member" | "viewer")
              }
              className="rounded-md border border-zinc-700 bg-zinc-800 px-2 py-1.5 text-sm text-zinc-100"
            >
              <option value="viewer">Viewer</option>
              {activeTeam.role === "owner" && (
                <option value="member">Member</option>
              )}
            </select>
            <button
              onClick={() =>
                inviteMutation.mutate({
                  teamId: activeTeamId,
                  email: inviteEmail,
                  teamRole: inviteRole,
                })
              }
              disabled={!inviteEmail || inviteMutation.isPending}
              className="rounded-md bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white transition-colors hover:bg-emerald-500 disabled:opacity-50"
            >
              Invite
            </button>
          </div>
          {inviteMutation.error && (
            <p className="mt-1 text-xs text-red-400">
              {inviteMutation.error.message}
            </p>
          )}
        </div>
      )}

      <div>
        <h3 className="mb-2 text-sm font-medium text-zinc-300">Your Role</h3>
        <span className="inline-block rounded bg-zinc-700 px-2 py-0.5 text-xs font-medium capitalize text-zinc-200">
          {activeTeam.role}
        </span>
      </div>
    </div>
  );
}
