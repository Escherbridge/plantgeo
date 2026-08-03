import { Suspense } from "react";
import type { Metadata } from "next";
import { ResetPasswordView } from "./ResetPasswordView";

export const metadata: Metadata = { title: "Reset password — PlantGeo" };

export default function ResetPasswordPage() {
  return (
    <Suspense fallback={<div className="animate-pulse text-sm text-zinc-500">Loading…</div>}>
      <ResetPasswordView />
    </Suspense>
  );
}
