"use client";

import { useState } from "react";

/** Requests a reset link. Always renders the same neutral outcome — never reveals account existence. */
export function ForgotPasswordForm() {
  const [email, setEmail] = useState("");
  const [loading, setLoading] = useState(false);
  const [submitted, setSubmitted] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (loading || submitted) return;
    setLoading(true);
    try {
      await fetch("/api/auth/forgot-password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email }),
      });
    } catch {
      // Intentionally ignored: the UI must not reveal whether the request succeeded.
    } finally {
      setLoading(false);
      setSubmitted(true);
    }
  }

  if (submitted) {
    return (
      <div className="flex flex-col items-center gap-2 text-center">
        <p className="text-sm text-emerald-400">
          If an account exists for that email, we&apos;ve sent a reset link.
        </p>
        <p className="text-xs text-zinc-500">Check your inbox and spam folder.</p>
      </div>
    );
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-4 w-full max-w-sm" noValidate>
      <div className="flex flex-col gap-1">
        <label className="text-sm text-zinc-300" htmlFor="forgot-email">
          Email
        </label>
        <input
          id="forgot-email"
          type="email"
          autoComplete="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          className="rounded-md bg-zinc-800 border border-zinc-700 px-3 py-2 text-sm text-zinc-100 placeholder-zinc-500 focus:outline-none focus:ring-1 focus:ring-emerald-500"
        />
      </div>
      <button
        type="submit"
        disabled={loading}
        className="rounded-md bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 px-4 py-2 text-sm font-medium text-white transition-colors"
      >
        {loading ? "Sending…" : "Send reset link"}
      </button>
    </form>
  );
}
