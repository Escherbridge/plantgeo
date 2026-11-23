"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { RegisterForm } from "@/components/auth/RegisterForm";
import { DEFAULT_CALLBACK_URL, safeCallbackUrl } from "@/lib/auth/callback-url";

/** Reads callbackUrl and renders the registration flow, threading it through to /login. */
export function RegisterView() {
  const searchParams = useSearchParams();
  const callbackUrl = safeCallbackUrl(searchParams.get("callbackUrl"));
  const loginHref =
    callbackUrl !== DEFAULT_CALLBACK_URL
      ? `/login?callbackUrl=${encodeURIComponent(callbackUrl)}`
      : "/login";

  return (
    <div className="flex flex-col gap-6">
      <div className="text-center">
        <h1 className="text-xl text-zinc-100 [font-family:var(--font-auth-display)]">
          Create an account
        </h1>
        <p className="mt-1 text-xs text-zinc-500">Create an account to save site plans and come back to them.</p>
      </div>
      <RegisterForm callbackUrl={callbackUrl} />
      <p className="text-center text-xs text-zinc-500">
        Already have an account?{" "}
        <Link href={loginHref} className="text-emerald-400 transition-colors hover:text-emerald-300">
          Sign in
        </Link>
      </p>
    </div>
  );
}
