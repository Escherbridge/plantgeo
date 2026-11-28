"use client";

import { useState } from "react";
import { AffiliateReviewList } from "@/app/dashboard/admin/affiliates/AffiliateReviewList";
import { trpc } from "@/lib/trpc/client";

/** Loads the review list and sends approve or decline. The server procedure is the gate. */
export function AffiliateReviewScreen() {
  const list = trpc.affiliates.listApplications.useQuery();
  const [pendingId, setPendingId] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const approve = trpc.affiliates.approve.useMutation({
    onSuccess: (result) => {
      setNotice(`Code ${result.code} issued.`);
      void list.refetch();
    },
    onError: (error) => setNotice(error.message),
    onSettled: () => setPendingId(null),
  });
  const decline = trpc.affiliates.decline.useMutation({
    onSuccess: () => {
      setNotice("Application declined.");
      void list.refetch();
    },
    onError: (error) => setNotice(error.message),
    onSettled: () => setPendingId(null),
  });

  if (list.isLoading) {
    return <p className="text-sm text-zinc-400">Loading applications.</p>;
  }
  if (list.isError) {
    return (
      <div className="flex flex-col items-start gap-3">
        <p className="text-sm text-red-400">{list.error.message}</p>
        <button
          type="button"
          className="rounded-md bg-zinc-800 px-3 py-1.5 text-xs font-medium text-zinc-200 hover:bg-zinc-700"
          onClick={() => void list.refetch()}
        >
          Try again
        </button>
      </div>
    );
  }

  return (
    <AffiliateReviewList
      applications={list.data ?? []}
      pendingId={pendingId}
      notice={notice}
      onApprove={(applicationId) => {
        setPendingId(applicationId);
        setNotice(null);
        approve.mutate({ applicationId });
      }}
      onDecline={(applicationId) => {
        setPendingId(applicationId);
        setNotice(null);
        decline.mutate({ applicationId });
      }}
    />
  );
}
