"use client";

import { Bell } from "lucide-react";
import { useUnreadAlertCount } from "@/hooks/useUnreadAlertCount";
import { usePanelStore } from "@/stores/panel-store";

/**
 * The toolbar's shortcut to the dock's Alerts section.
 *
 * It opened its own right-hand sheet until 2026-08-08. The alert list now lives where every
 * other report does -- one section of the left dock -- and this stays as what it always
 * usefully was: an always-visible unread count, and one click to the thing it counts. The
 * count is read through the shared hook, so the bell and the dock's Alerts badge are two
 * views of one query rather than two polls of one number.
 */
export function AlertBell() {
  const count = useUnreadAlertCount();
  const focusDockSection = usePanelStore((state) => state.focusDockSection);

  return (
    <button
      type="button"
      onClick={() => focusDockSection("alerts")}
      // p-2 around a 20px icon is a 36px hit area; max-sm:p-3 brings it to the 44px
      // mobile minimum without changing the icon's own size or the desktop footprint.
      className="relative rounded-md p-2 transition-colors hover:bg-[hsl(var(--accent))] max-sm:p-3"
      aria-label={count > 0 ? `${count} unread alerts` : "Alerts"}
    >
      <Bell className="h-5 w-5 text-[hsl(var(--foreground))]" />
      {count > 0 && (
        <span
          className="absolute -right-0.5 -top-0.5 flex h-4.5 min-w-4.5 items-center justify-center rounded-full bg-red-500 px-1 text-[10px] font-bold leading-none text-white"
          aria-hidden="true"
        >
          {count > 99 ? "99+" : count}
        </span>
      )}
    </button>
  );
}
