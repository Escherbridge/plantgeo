"use client";

import { useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";

type VerifyState = "pending" | "success" | "error";

/** Reads the verification token from the query string and confirms it on mount. */
export function VerifyEmailView() {
  const searchParams = useSearchParams();
  const token = searchParams.get("token");
  const [state, setState] = useState<VerifyState>(token ? "pending" : "error");
  const [message, setMessage] = useState<string | null>(
    token ? null : "This verification link is missing or malformed."
  );

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch("/api/auth/verify-email", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ token }),
        });
        if (cancelled) return;
        if (res.ok) {
          setState("success");
        } else {
          const data = await res.json().catch(() => ({}));
          setMessage(
            (data as { error?: string }).error ?? "This verification link is invalid or has expired."
          );
          setState("error");
        }
      } catch {
        if (!cancelled) {
          setMessage("Something went wrong. Please try again.");
          setState("error");
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [token]);

  return (
    <div className="flex flex-col items-center gap-4 text-center">
      <h2 className="text-xl text-zinc-100 [font-family:var(--font-auth-display)]">
        Verify your email
      </h2>

      {state === "pending" && (
        <div className="flex flex-col items-center gap-3 py-4">
          <span
            aria-hidden
            className="h-6 w-6 animate-spin rounded-full border-2 border-zinc-700 border-t-emerald-500"
          />
          <p className="text-xs text-zinc-500">Confirming your email address…</p>
        </div>
      )}

      {state === "success" && (
        <>
          <p className="text-sm text-emerald-400">Your email has been verified.</p>
          <Link
            href="/dashboard"
            className="rounded-md bg-emerald-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-emerald-500"
          >
            Go to dashboard
          </Link>
        </>
      )}

      {state === "error" && (
        <>
          <p role="alert" className="text-sm text-red-400">
            {message}
          </p>
          <Link href="/login" className="text-xs text-emerald-400 transition-colors hover:text-emerald-300">
            Back to sign in
          </Link>
        </>
      )}
    </div>
  );
}
