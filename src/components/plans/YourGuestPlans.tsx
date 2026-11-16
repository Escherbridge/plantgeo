"use client";

import Link from "next/link";
import { useEffect, useSyncExternalStore } from "react";
import { editorialFocusRing } from "@/components/ui/editorial";
import {
  guestPlanEditPath,
  guestPlanSnapshot,
  migrateStoredGuestPlans,
  subscribeSavedGuestPlans,
  type SavedGuestPlan,
} from "@/lib/plans/guest-plan-links";
import { cn } from "@/lib/utils";

const EMPTY: SavedGuestPlan[] = [];

const planLinkClass = cn(
  "font-editorial-text text-caption text-ink underline decoration-accent decoration-2 underline-offset-4",
  editorialFocusRing
);

export function useSavedGuestPlans(): SavedGuestPlan[] {
  const plans = useSyncExternalStore(subscribeSavedGuestPlans, guestPlanSnapshot, () => EMPTY);
  useEffect(() => {
    try {
      migrateStoredGuestPlans(window.localStorage);
    } catch {
      // Leave the stored value in place when this browser refuses the write.
    }
  }, []);
  return plans;
}

export function YourGuestPlans() {
  const plans = useSavedGuestPlans();
  if (plans.length === 0) return null;
  if (plans.length === 1) {
    return (
      <Link href={guestPlanEditPath(plans[0])} className={planLinkClass}>
        Your guest plan
      </Link>
    );
  }
  return (
    <div className="flex flex-col gap-tight">
      <p className="font-editorial-label text-label text-ink-muted uppercase">Your guest plans</p>
      <ul className="flex flex-col gap-tight">
        {plans.map((plan) => (
          <li key={plan.publicId}>
            <Link href={guestPlanEditPath(plan)} className={planLinkClass}>
              {plan.title}
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Visible on the home map and on the crawlable home sheet. One copy, above both. */
export function HomeGuestPlans() {
  const plans = useSavedGuestPlans();
  if (plans.length === 0) return null;
  return (
    <div className="pointer-events-auto fixed top-[calc(var(--application-top-bar-height)+0.75rem)] left-1/2 z-[35] w-[min(24rem,calc(100%-2rem))] -translate-x-1/2 bg-paper px-snug py-snug rule-all-hairline border-rule">
      <YourGuestPlans />
    </div>
  );
}
