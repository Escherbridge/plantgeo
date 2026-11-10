"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { editorialFocusRing, editorialFocusRingInverse } from "@/components/ui/editorial";
import { trpc } from "@/lib/trpc/client";
import { cn } from "@/lib/utils";

export function NewPlanForm() {
  const router = useRouter();
  const [title, setTitle] = useState("Site plan");
  const [notice, setNotice] = useState<string | null>(null);
  const createPlan = trpc.plans.create.useMutation();

  return (
    <form
      className="flex max-w-measure flex-col gap-snug"
      onSubmit={(event) => {
        event.preventDefault();
        setNotice(null);
        void createPlan
          .mutateAsync({ title })
          .then((created) => {
            const params = new URLSearchParams();
            if (created.guestToken) params.set("token", created.guestToken);
            const query = params.toString();
            router.push(query ? `/plan/${created.publicId}?${query}` : `/plan/${created.publicId}`);
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
    </form>
  );
}
