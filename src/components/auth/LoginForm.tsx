"use client";

import { useState } from "react";
import { signIn } from "next-auth/react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { DEFAULT_CALLBACK_URL, safeCallbackUrl } from "@/lib/auth/callback-url";

/** Credentials + OAuth sign-in form. Redirects to callbackUrl on success. */
export function LoginForm({
  callbackUrl = DEFAULT_CALLBACK_URL,
}: {
  callbackUrl?: string;
}) {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  // Re-validated here as well as in the view: this prop is reachable from any
  // caller, and the credentials path navigates without NextAuth's own check.
  const destination = safeCallbackUrl(callbackUrl);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (loading) return;
    setError(null);
    setLoading(true);
    const result = await signIn("credentials", {
      email,
      password,
      redirect: false,
    });
    setLoading(false);
    if (result?.status === 429) {
      setError("Too many sign-in attempts. Wait a minute and try again.");
    } else if (result?.status === 503) {
      setError("Sign-in is temporarily unavailable. Please try again shortly.");
    } else if (result?.error) {
      setError("Invalid email or password.");
    } else {
      router.push(destination);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-4 w-full max-w-sm" noValidate>
      <div className="flex flex-col gap-1">
        <label className="text-sm text-zinc-300" htmlFor="email">
          Email
        </label>
        <input
          id="email"
          type="email"
          autoComplete="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          aria-invalid={Boolean(error)}
          aria-describedby={error ? "login-error" : undefined}
          className="rounded-md bg-zinc-800 border border-zinc-700 px-3 py-2 text-sm text-zinc-100 placeholder-zinc-500 focus:outline-none focus:ring-1 focus:ring-emerald-500"
        />
      </div>
      <div className="flex flex-col gap-1">
        <div className="flex items-center justify-between">
          <label className="text-sm text-zinc-300" htmlFor="password">
            Password
          </label>
          <Link
            href="/forgot-password"
            className="text-xs text-emerald-400 transition-colors hover:text-emerald-300"
          >
            Forgot password?
          </Link>
        </div>
        <input
          id="password"
          type="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          aria-invalid={Boolean(error)}
          aria-describedby={error ? "login-error" : undefined}
          className="rounded-md bg-zinc-800 border border-zinc-700 px-3 py-2 text-sm text-zinc-100 placeholder-zinc-500 focus:outline-none focus:ring-1 focus:ring-emerald-500"
        />
      </div>
      {error && (
        <p id="login-error" role="alert" className="text-sm text-red-400">
          {error}
        </p>
      )}
      <button
        type="submit"
        disabled={loading}
        className="rounded-md bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 px-4 py-2 text-sm font-medium text-white transition-colors"
      >
        {loading ? "Signing in…" : "Sign in"}
      </button>
      <div className="flex flex-col gap-2">
        <button
          type="button"
          onClick={() => signIn("google", { callbackUrl: destination })}
          className="rounded-md bg-zinc-800 hover:bg-zinc-700 border border-zinc-700 px-4 py-2 text-sm text-zinc-200 transition-colors"
        >
          Continue with Google
        </button>
        <button
          type="button"
          onClick={() => signIn("github", { callbackUrl: destination })}
          className="rounded-md bg-zinc-800 hover:bg-zinc-700 border border-zinc-700 px-4 py-2 text-sm text-zinc-200 transition-colors"
        >
          Continue with GitHub
        </button>
      </div>
    </form>
  );
}
