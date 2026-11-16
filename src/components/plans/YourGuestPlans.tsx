"use client";

import { useSyncExternalStore } from "react";
import { editorialFocusRing } from "@/components/ui/editorial";
import {
  guestPlanEditPath,
  guestPlanSnapshot,
  subscribeSavedGuestPlans,
  type SavedGuestPlan,
} from "@/lib/plans/guest-plan-links";
import { cn } from "@/lib/utils";

const EMPTY: SavedGuestPlan[] = [];

export function useSavedGuestPlans(): SavedGuestPlan[] {
  return useSyncExternalStore(subscribeSavedGuestPlans, guestPlanSnapshot, () => EMPTY);
}

export function YourGuestPlans() {
  const plans = useSavedGuestPlans();
  if (plans.length === 0) return null;

  return (
    <div className="flex flex-col gap-tight">
      {plans.map((plan, index) => (
        <a
          key={plan.publicId}
          href={guestPlanEditPath(plan)}
          className={cn(
            "font-editorial-text text-caption text-ink underline decoration-accent decoration-2 underline-offset-4",
            editorialFocusRing
          )}
        >
          {plans.length === 1 || index === 0 ? "Your guest plan" : `Your guest plan, ${plan.title}`}
        </a>
      ))}
    </div>
  );
}
