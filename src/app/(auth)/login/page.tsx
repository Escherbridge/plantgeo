import { Suspense } from "react";
import type { Metadata } from "next";
import { LoginView } from "./LoginView";

export const metadata: Metadata = { title: "Sign in — PlantGeo" };

export default function LoginPage() {
  return (
    <Suspense fallback={<div className="animate-pulse text-sm text-zinc-500">Loading…</div>}>
      <LoginView />
    </Suspense>
  );
}
