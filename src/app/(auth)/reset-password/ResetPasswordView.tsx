"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { ResetPasswordForm } from "@/components/auth/ResetPasswordForm";
import { FormNotice } from "@/components/auth/FormNotice";

/** Reads the reset token from the query string and renders the reset flow. */
export function ResetPasswordView() {
  const searchParams = useSearchParams();
  const token = searchParams.get("token");

  return (
    <div className="flex flex-col gap-6">
      <div className="text-center">
        <h2 className="text-xl text-zinc-100 [font-family:var(--font-auth-display)]">
          Reset your password
        </h2>
        <p className="mt-1 text-xs text-zinc-500">Choose a new password for your account.</p>
      </div>

      {token ? (
        <ResetPasswordForm token={token} />
      ) : (
        <div className="flex flex-col items-center gap-3 text-center">
          <FormNotice tone="error">This reset link is missing or malformed.</FormNotice>
          <Link
            href="/forgot-password"
            className="text-xs text-emerald-400 transition-colors hover:text-emerald-300"
          >
            Request a new reset link
          </Link>
        </div>
      )}
    </div>
  );
}
