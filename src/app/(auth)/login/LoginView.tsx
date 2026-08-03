"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { LoginForm } from "@/components/auth/LoginForm";
import { FormNotice } from "@/components/auth/FormNotice";
import { DEFAULT_CALLBACK_URL, safeCallbackUrl } from "@/lib/auth/callback-url";

const SIGN_IN_ERROR_MESSAGES: Record<string, string> = {
  CredentialsSignin: "Invalid email or password.",
  OAuthAccountNotLinked: "That email is already registered with a different sign-in method.",
  AccessDenied: "Access was denied.",
  Verification: "That sign-in link is no longer valid. Request a new one.",
  RateLimited: "Too many sign-in attempts. Wait a minute and try again.",
  SignInUnavailable: "Sign-in is temporarily unavailable. Please try again shortly.",
};

/** Reads registered/reset/error/callbackUrl query state and renders the sign-in flow. */
export function LoginView() {
  const searchParams = useSearchParams();
  const callbackUrl = safeCallbackUrl(searchParams.get("callbackUrl"));
  const registered = searchParams.get("registered") === "1";
  const reset = searchParams.get("reset") === "1";
  const errorCode = searchParams.get("error");
  const errorMessage = errorCode
    ? SIGN_IN_ERROR_MESSAGES[errorCode] ?? "Something went wrong while signing in. Please try again."
    : null;

  return (
    <div className="flex flex-col gap-6">
      <div className="text-center">
        <h2 className="text-xl text-zinc-100 [font-family:var(--font-auth-display)]">Sign in</h2>
        <p className="mt-1 text-xs text-zinc-500">Access your monitoring dashboard.</p>
      </div>

      {(registered || reset || errorMessage) && (
        <div className="flex flex-col gap-2">
          {registered && (
            <FormNotice tone="success">
              Check your email to finish setting up your account before signing in.
            </FormNotice>
          )}
          {reset && <FormNotice tone="success">Your password has been reset. Sign in below.</FormNotice>}
          {errorMessage && <FormNotice tone="error">{errorMessage}</FormNotice>}
        </div>
      )}

      <LoginForm callbackUrl={callbackUrl} />

      <div className="flex items-center justify-center gap-1 text-xs text-zinc-500">
        <span>New here?</span>
        <Link
          href={
            callbackUrl !== DEFAULT_CALLBACK_URL
              ? `/register?callbackUrl=${encodeURIComponent(callbackUrl)}`
              : "/register"
          }
          className="text-emerald-400 transition-colors hover:text-emerald-300"
        >
          Create an account
        </Link>
      </div>
    </div>
  );
}
