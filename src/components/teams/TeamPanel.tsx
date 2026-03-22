"use client";

import { useState } from "react";
import { trpc } from "@/lib/trpc/client";
import { useAuthStore } from "@/stores/auth-store";

export function TeamPanel() {
  const { activeTeamId } = useAuthStore();
  const [inviteUserId, setInviteUserId] = useState("");
  const [inviteRole, setInviteRole] = useState<"owner" | "member" | "viewer">("member");
  const utils = trpc.useUtils();

  const { data: myTeams } = trpc.teams.listMyTeams.useQuery();
  const activeTeam = myTeams?.find((t) => t.team.id === activeTeamId);

  const inviteMutation = trpc.teams.inviteMember.useMutation({
    onSuccess: () => {
      setInviteUserId("");
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

  return (
    <div className="flex flex-col gap-4 p-4">
      <div>
        <h2 className="text-base font-semibold text-zinc-100">{activeTeam.team.name}</h2>
        {activeTeam.team.description && (
          <p className="mt-1 text-sm text-zinc-400">{activeTeam.team.description}</p>
        )}
      </div>

      <div>
        <h3 className="text-sm font-medium text-zinc-300 mb-2">Invite Member</h3>
        <div className="flex gap-2">
          <input
            type="text"
            placeholder="User ID"
            value={inviteUserId}
            onChange={(e) => setInviteUserId(e.target.value)}
            className="flex-1 rounded-md bg-zinc-800 border border-zinc-700 px-3 py-1.5 text-sm text-zinc-100 placeholder-zinc-500 focus:outline-none focus:ring-1 focus:ring-emerald-500"
          />
          <select
            value={inviteRole}
            onChange={(e) => setInviteRole(e.target.value as "owner" | "member" | "viewer")}
            className="rounded-md bg-zinc-800 border border-zinc-700 px-2 py-1.5 text-sm text-zinc-100"
          >
            <option value="viewer">Viewer</option>
            <option value="member">Member</option>
            <option value="owner">Owner</option>
          </select>
          <button
            onClick={() =>
              inviteMutation.mutate({
                teamId: activeTeamId,
                userId: inviteUserId,
                teamRole: inviteRole,
              })
            }
            disabled={!inviteUserId || inviteMutation.isPending}
            className="rounded-md bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 px-3 py-1.5 text-sm font-medium text-white transition-colors"
          >
            Invite
          </button>
        </div>
        {inviteMutation.error && (
          <p className="mt-1 text-xs text-red-400">{inviteMutation.error.message}</p>
        )}
      </div>

      <div>
        <h3 className="text-sm font-medium text-zinc-300 mb-2">Your Role</h3>
        <span className="inline-block rounded px-2 py-0.5 text-xs font-medium bg-zinc-700 text-zinc-200 capitalize">
          {activeTeam.role}
        </span>
      </div>
    </div>
  );
}
