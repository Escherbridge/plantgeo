"use client";

import { useState } from "react";
import { Bell } from "lucide-react";
import { trpc } from "@/lib/trpc/client";
import { AlertPanel } from "@/components/panels/AlertPanel";

/** True when a NextAuth session cookie exists; unsigned visitors get no cookie. */
function hasSessionCookie(): boolean {
  if (typeof document === "undefined") return false;
  return /(?:^|;\s*)(?:__Secure-)?(?:next-auth|authjs)\.session-token=/.test(
    document.cookie
  );
}

export function AlertBell() {
  const [panelOpen, setPanelOpen] = useState(false);

  const { data: unreadCount, refetch } = trpc.alerts.getUnreadCount.useQuery(undefined, {
    // Signed-out visitors have no alerts to poll; skipping the query avoids a
    // guaranteed 401 every 30 seconds.
    enabled: hasSessionCookie(),
    // Poll every 30 seconds for new alerts
    refetchInterval: 30_000,
    // Don't refetch on window focus to avoid excess requests
    refetchOnWindowFocus: false,
  });

  const count = unreadCount ?? 0;

  return (
    <>
      <button
        type="button"
        onClick={() => setPanelOpen(true)}
        className="relative p-2 rounded-md hover:bg-[hsl(var(--accent))] transition-colors"
        aria-label={count > 0 ? `${count} unread alerts` : "Alerts"}
      >
        <Bell className="h-5 w-5 text-[hsl(var(--foreground))]" />
        {count > 0 && (
          <span
            className="absolute -top-0.5 -right-0.5 min-w-[18px] h-[18px] flex items-center justify-center rounded-full bg-red-500 text-white text-[10px] font-bold px-1 leading-none"
            aria-hidden="true"
          >
            {count > 99 ? "99+" : count}
          </span>
        )}
      </button>

      <AlertPanel
        open={panelOpen}
        onOpenChange={setPanelOpen}
        onMarkRead={() => refetch()}
      />
    </>
  );
}
