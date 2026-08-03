"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useSession } from "next-auth/react";
import { AlertTriangle, Loader2, LogOut, Trash2, Users } from "lucide-react";
import { trpc } from "@/lib/trpc/client";
import { toast } from "@/components/ui/toast";
import { cn } from "@/lib/utils";
import { useActiveOrganization } from "../useActiveOrganization";

type TeamRole = "owner" | "member" | "viewer";
const ROLE_OPTIONS: TeamRole[] = ["owner", "member", "viewer"];

/** Member roster with role changes, removal, and leave-organization for the active org. */
export default function OrganizationMembersPage() {
  const router = useRouter();
  const { data: session } = useSession();
  const { organization, role, isLoading: orgLoading } = useActiveOrganization();
  const utils = trpc.useUtils();

  const teamId = organization?.id ?? "";
  const membersQuery = trpc.teams.listMembers.useQuery({ teamId }, { enabled: Boolean(teamId) });

  const updateRoleMutation = trpc.teams.updateMemberRole.useMutation({
    onSuccess: () => utils.teams.listMembers.invalidate({ teamId }),
  });
  const removeMemberMutation = trpc.teams.removeMember.useMutation({
    onSuccess: () => utils.teams.listMembers.invalidate({ teamId }),
  });
  const leaveTeamMutation = trpc.teams.leaveTeam.useMutation();

  const [confirmingRemoval, setConfirmingRemoval] = useState<string | null>(null);
  const [confirmingLeave, setConfirmingLeave] = useState(false);

  if (orgLoading || membersQuery.isLoading) {
    return (
      <div className="flex flex-col items-center gap-3 py-16 text-center">
        <Loader2 className="h-5 w-5 animate-spin text-emerald-500" />
        <p className="text-sm text-zinc-400">Loading members…</p>
      </div>
    );
  }

  if (membersQuery.isError) {
    return (
      <div className="flex flex-col items-center gap-3 py-16 text-center">
        <p className="text-sm text-red-400">{membersQuery.error.message}</p>
        <button
          type="button"
          onClick={() => membersQuery.refetch()}
          className="rounded-md bg-zinc-800 px-3 py-1.5 text-xs font-medium text-zinc-200 transition-colors hover:bg-zinc-700"
        >
          Try again
        </button>
      </div>
    );
  }

  const members = membersQuery.data ?? [];
  const ownerCount = members.filter((m) => m.teamRole === "owner").length;
  const isOwner = role === "owner";
  const currentUserId = session?.user?.id;

  function isLastOwner(memberUserId: string, memberRole: string | null): boolean {
    return memberRole === "owner" && ownerCount <= 1 && memberUserId === currentUserId;
  }

  async function handleRoleChange(userId: string, nextRole: TeamRole) {
    try {
      await updateRoleMutation.mutateAsync({ teamId, userId, teamRole: nextRole });
    } catch (err) {
      toast.error("Couldn't change role", { description: (err as Error).message });
    }
  }

  async function handleRemove(userId: string) {
    try {
      await removeMemberMutation.mutateAsync({ teamId, userId });
      toast.success("Member removed");
    } catch (err) {
      toast.error("Couldn't remove member", { description: (err as Error).message });
    } finally {
      setConfirmingRemoval(null);
    }
  }

  async function handleLeave() {
    try {
      await leaveTeamMutation.mutateAsync({ teamId });
      await utils.teams.listMyTeams.invalidate();
      const remaining = await utils.teams.listMyTeams.fetch();
      if (remaining && remaining.length > 0) {
        router.push("/dashboard/org");
      } else {
        router.push("/onboarding");
      }
    } catch (err) {
      toast.error("Couldn't leave organization", { description: (err as Error).message });
      setConfirmingLeave(false);
    }
  }

  const currentUserIsLastOwner = Boolean(
    currentUserId &&
      isOwner &&
      ownerCount <= 1 &&
      members.some((m) => m.userId === currentUserId && m.teamRole === "owner")
  );

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center gap-2">
        <Users className="h-4 w-4 text-emerald-400" />
        <h1 className="text-lg text-zinc-100 [font-family:var(--font-onboarding-display,inherit)]">
          Members
        </h1>
        <span className="text-xs text-zinc-500">({members.length})</span>
      </div>

      {members.length === 0 ? (
        <p className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-6 text-center text-sm text-zinc-500">
          No members found.
        </p>
      ) : (
        <div className="overflow-hidden rounded-lg border border-zinc-800">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-zinc-800 bg-zinc-900/60 text-left text-xs uppercase tracking-wide text-zinc-500">
                <th className="px-4 py-2.5 font-medium">Name</th>
                <th className="px-4 py-2.5 font-medium">Email</th>
                <th className="px-4 py-2.5 font-medium">Joined</th>
                <th className="px-4 py-2.5 font-medium">Role</th>
                {isOwner && <th className="px-4 py-2.5 font-medium" />}
              </tr>
            </thead>
            <tbody>
              {members.map((member) => {
                const lastOwner = isLastOwner(member.userId, member.teamRole);
                const isSelf = member.userId === currentUserId;
                return (
                  <tr key={member.userId} className="border-b border-zinc-900 bg-zinc-900/20 last:border-0">
                    <td className="px-4 py-2.5 text-zinc-200">
                      {member.name}
                      {isSelf && <span className="ml-1.5 text-xs text-zinc-500">(you)</span>}
                    </td>
                    <td className="px-4 py-2.5 text-zinc-400">{member.email}</td>
                    <td className="px-4 py-2.5 text-zinc-500">
                      {member.joinedAt ? new Date(member.joinedAt).toLocaleDateString() : "—"}
                    </td>
                    <td className="px-4 py-2.5">
                      {isOwner ? (
                        <select
                          value={member.teamRole ?? "member"}
                          disabled={lastOwner || updateRoleMutation.isPending}
                          onChange={(e) => handleRoleChange(member.userId, e.target.value as TeamRole)}
                          title={lastOwner ? "An organization must retain at least one owner" : undefined}
                          className="rounded-md border border-zinc-700 bg-zinc-800 px-2 py-1 text-xs capitalize text-zinc-100 disabled:opacity-50"
                        >
                          {ROLE_OPTIONS.map((r) => (
                            <option key={r} value={r}>
                              {r}
                            </option>
                          ))}
                        </select>
                      ) : (
                        <span className="rounded bg-zinc-800 px-2 py-0.5 text-xs font-medium capitalize text-zinc-200">
                          {member.teamRole}
                        </span>
                      )}
                    </td>
                    {isOwner && (
                      <td className="px-4 py-2.5 text-right">
                        {confirmingRemoval === member.userId ? (
                          <div className="flex items-center justify-end gap-1.5">
                            <span className="text-xs text-zinc-500">Remove?</span>
                            <button
                              type="button"
                              onClick={() => handleRemove(member.userId)}
                              disabled={removeMemberMutation.isPending}
                              className="rounded bg-red-600 px-2 py-1 text-xs font-medium text-white transition-colors hover:bg-red-500 disabled:opacity-50"
                            >
                              Confirm
                            </button>
                            <button
                              type="button"
                              onClick={() => setConfirmingRemoval(null)}
                              className="rounded bg-zinc-800 px-2 py-1 text-xs text-zinc-300 transition-colors hover:bg-zinc-700"
                            >
                              Cancel
                            </button>
                          </div>
                        ) : (
                          <button
                            type="button"
                            disabled={lastOwner}
                            title={lastOwner ? "An organization must retain at least one owner" : "Remove member"}
                            onClick={() => setConfirmingRemoval(member.userId)}
                            className="rounded p-1.5 text-zinc-500 transition-colors hover:bg-red-500/10 hover:text-red-400 disabled:cursor-not-allowed disabled:opacity-30 disabled:hover:bg-transparent disabled:hover:text-zinc-500"
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </button>
                        )}
                      </td>
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <div className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-4">
        {currentUserIsLastOwner ? (
          <div className="flex items-start gap-2 text-xs text-zinc-500">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-400" />
            You&rsquo;re the only owner, so you can&rsquo;t leave until you promote another member to owner.
          </div>
        ) : confirmingLeave ? (
          <div className="flex items-center gap-2">
            <span className="text-sm text-zinc-300">Leave this organization?</span>
            <button
              type="button"
              onClick={handleLeave}
              disabled={leaveTeamMutation.isPending}
              className="flex items-center gap-1.5 rounded-md bg-red-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-red-500 disabled:opacity-50"
            >
              {leaveTeamMutation.isPending && <Loader2 className="h-3 w-3 animate-spin" />}
              Confirm leave
            </button>
            <button
              type="button"
              onClick={() => setConfirmingLeave(false)}
              className="rounded-md bg-zinc-800 px-3 py-1.5 text-xs text-zinc-300 transition-colors hover:bg-zinc-700"
            >
              Cancel
            </button>
          </div>
        ) : (
          <button
            type="button"
            onClick={() => setConfirmingLeave(true)}
            className={cn(
              "flex items-center gap-1.5 text-sm text-zinc-400 transition-colors hover:text-red-400"
            )}
          >
            <LogOut className="h-3.5 w-3.5" />
            Leave organization
          </button>
        )}
      </div>
    </div>
  );
}
