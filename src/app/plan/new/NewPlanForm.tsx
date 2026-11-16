"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { GuestLinkPanel } from "@/components/plans/GuestLinkPanel";
import { YourGuestPlans } from "@/components/plans/YourGuestPlans";
import { editorialFocusRing, editorialFocusRingInverse } from "@/components/ui/editorial";
import { rememberGuestPlan, type SavedGuestPlan } from "@/lib/plans/guest-plan-links";
import { trpc } from "@/lib/trpc/client";
import { cn } from "@/lib/utils";

export function NewPlanForm() {
  const router = useRouter();
  const [title, setTitle] = useState("Site plan");
  const [notice, setNotice] = useState<string | null>(null);
  const [keptGuest, setKeptGuest] = useState<SavedGuestPlan | null>(null);
  const createPlan = trpc.plans.create.useMutation();

  if (keptGuest) {
    return (
      <GuestLinkPanel
        publicId={keptGuest.publicId}
        guestToken={keptGuest.token}
        title={keptGuest.title}
        plain
      />
    );
  }

  return (
    <form
      className="flex max-w-measure flex-col gap-snug"
      onSubmit={(event) => {
        event.preventDefault();
        setNotice(null);
        void createPlan
          .mutateAsync({ title })
          .then((created) => {
            if (created.guestToken) {
              const saved = { publicId: created.publicId, token: created.guestToken, title };
              try {
                rememberGuestPlan(window.localStorage, saved);
              } catch {
                // The panel still shows the link when storage is unavailable.
              }
              setKeptGuest(saved);
              return;
            }
            router.push(`/plan/${created.publicId}`);
          })
          .catch(() => {
            setNotice("The plan could not be started. Try again in a moment.");
          });
      }}
    >
      <label className="font-editorial-label text-label text-ink-muted uppercase" htmlFor="new-plan-title">
        Plan name
      </label>
      <input
        id="new-plan-title"
        value={title}
        onChange={(event) => setTitle(event.target.value)}
        maxLength={200}
        required
        className={cn(
          "editorial-flat rule-all-medium border-rule bg-paper px-snug py-tight font-editorial-text text-body text-ink",
          editorialFocusRing
        )}
      />
      <button
        type="submit"
        disabled={createPlan.isPending}
        className={cn(
          "bg-ink px-comfortable py-snug font-editorial-label text-label text-paper uppercase hover:bg-accent hover:text-accent-ink disabled:opacity-70",
          editorialFocusRingInverse
        )}
      >
        {createPlan.isPending ? "Starting" : "Start your plan"}
      </button>
      {notice ? (
        <p role="status" className="font-editorial-text text-caption text-ink">
          {notice}
        </p>
      ) : null}
      <YourGuestPlans />
    </form>
  );
}
