import { Suspense } from "react";
import type { Metadata } from "next";
import { RegisterView } from "./RegisterView";

export const metadata: Metadata = { title: "Create account — PlantGeo" };

export default function RegisterPage() {
  return (
    <Suspense fallback={<div className="animate-pulse text-sm text-zinc-500">Loading…</div>}>
      <RegisterView />
    </Suspense>
  );
}
