import { Suspense } from "react";
import type { Metadata } from "next";
import { VerifyEmailView } from "./VerifyEmailView";

export const metadata: Metadata = { title: "Verify email — PlantGeo" };

export default function VerifyEmailPage() {
  return (
    <Suspense fallback={<div className="animate-pulse text-sm text-zinc-500">Loading…</div>}>
      <VerifyEmailView />
    </Suspense>
  );
}
