"use client";

/**
 * The unread alert count, read once for the two surfaces that show it: the toolbar bell and
 * the dock's Alerts section. One hook rather than two call sites so both observe the same
 * react-query entry -- two copies with different options would poll the server twice for one
 * number, and could disagree about it in between.
 */

import { trpc } from "@/lib/trpc/client";

/** True when a NextAuth session cookie exists; unsigned visitors get no cookie. */
export function hasSessionCookie(): boolean {
  if (typeof document === "undefined") return false;
  return /(?:^|;\s*)(?:__Secure-)?(?:next-auth|authjs)\.session-token=/.test(document.cookie);
}

/** Unread alerts for the signed-in reader; 0 for everyone else. */
export function useUnreadAlertCount(): number {
  const { data } = trpc.alerts.getUnreadCount.useQuery(undefined, {
    // Signed-out visitors have no alerts to poll; skipping the query avoids a guaranteed
    // 401 every 30 seconds.
    enabled: hasSessionCookie(),
    refetchInterval: 30_000,
    // Refetching on focus would add a request per tab switch to a number that already polls.
    refetchOnWindowFocus: false,
  });
  return data ?? 0;
}
