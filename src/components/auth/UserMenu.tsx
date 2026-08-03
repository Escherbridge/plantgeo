"use client";

import { useState } from "react";
import { useSession, signOut } from "next-auth/react";
import { TeamSwitcher } from "@/components/teams/TeamSwitcher";

/**
 * Signed-in identity badge with sign-out and the organization switcher.
 * Exported for the shell layout to mount.
 */
export function UserMenu() {
  const { data: session, status } = useSession();
  const [signingOut, setSigningOut] = useState(false);

  if (status !== "authenticated" || !session?.user) return null;

  async function handleSignOut() {
    if (signingOut) return;
    setSigningOut(true);
    await signOut({ callbackUrl: "/login" });
  }

  return (
    <div className="flex items-center gap-3 rounded-md border border-zinc-800 bg-zinc-900/70 px-3 py-2 text-sm">
      <TeamSwitcher />
      <span className="truncate text-zinc-300" title={session.user.email ?? undefined}>
        {session.user.email}
      </span>
      <button
        type="button"
        onClick={handleSignOut}
        disabled={signingOut}
        className="rounded-md bg-zinc-800 hover:bg-zinc-700 disabled:opacity-50 px-3 py-1.5 text-xs font-medium text-zinc-200 transition-colors"
      >
        {signingOut ? "Signing out…" : "Sign out"}
      </button>
    </div>
  );
}
