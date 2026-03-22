"use client";

import { useState } from "react";
import { useSession } from "next-auth/react";
import { trpc } from "@/lib/trpc/client";

export function UserPanel() {
  const { data: session } = useSession();
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [pwMessage, setPwMessage] = useState<string | null>(null);

  const { data: myTeams } = trpc.teams.listMyTeams.useQuery(undefined, {
    enabled: !!session,
  });

  const user = session?.user as
    | { id?: string; name?: string | null; email?: string | null; platformRole?: string }
    | undefined;

  async function handlePasswordChange(e: React.FormEvent) {
    e.preventDefault();
    setPwMessage(null);
    const res = await fetch("/api/auth/password", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ currentPassword, newPassword }),
    });
    if (res.ok) {
      setPwMessage("Password updated.");
      setCurrentPassword("");
      setNewPassword("");
    } else {
      const data = await res.json().catch(() => ({}));
      setPwMessage((data as { error?: string }).error ?? "Failed to update password.");
    }
  }

  if (!session) {
    return <div className="p-4 text-sm text-zinc-400">Not signed in.</div>;
  }

  return (
    <div className="flex flex-col gap-6 p-4">
      <div>
        <h2 className="text-base font-semibold text-zinc-100">Profile</h2>
        <div className="mt-2 text-sm text-zinc-300 space-y-1">
          {user?.name && <div>{user.name}</div>}
          <div>{user?.email}</div>
          <div className="inline-flex items-center gap-1">
            <span className="text-zinc-500">Role:</span>
            <span className="capitalize text-emerald-400">{user?.platformRole ?? "contributor"}</span>
          </div>
        </div>
      </div>

      <div>
        <h3 className="text-sm font-medium text-zinc-300 mb-2">Change Password</h3>
        <form onSubmit={handlePasswordChange} className="flex flex-col gap-2">
          <input
            type="password"
            placeholder="Current password"
            value={currentPassword}
            onChange={(e) => setCurrentPassword(e.target.value)}
            required
            className="rounded-md bg-zinc-800 border border-zinc-700 px-3 py-1.5 text-sm text-zinc-100 placeholder-zinc-500 focus:outline-none focus:ring-1 focus:ring-emerald-500"
          />
          <input
            type="password"
            placeholder="New password (min 8 chars)"
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            required
            minLength={8}
            className="rounded-md bg-zinc-800 border border-zinc-700 px-3 py-1.5 text-sm text-zinc-100 placeholder-zinc-500 focus:outline-none focus:ring-1 focus:ring-emerald-500"
          />
          {pwMessage && (
            <p className="text-xs text-zinc-400">{pwMessage}</p>
          )}
          <button
            type="submit"
            className="rounded-md bg-emerald-600 hover:bg-emerald-500 px-3 py-1.5 text-sm font-medium text-white transition-colors"
          >
            Update Password
          </button>
        </form>
      </div>

      <div>
        <h3 className="text-sm font-medium text-zinc-300 mb-2">My Teams</h3>
        {myTeams?.length ? (
          <ul className="space-y-1">
            {myTeams.map(({ team, role }) => (
              <li key={team.id} className="flex items-center justify-between text-sm">
                <span className="text-zinc-200">{team.name}</span>
                <span className="text-xs text-zinc-500 capitalize">{role}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-zinc-500">No teams yet.</p>
        )}
      </div>
    </div>
  );
}
