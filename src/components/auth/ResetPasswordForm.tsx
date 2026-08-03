"use client";

import { useState } from "react";

const MIN_PASSWORD_LENGTH = 8;
// Mirrors MAX_BCRYPT_PASSWORD_BYTES in src/lib/server/security/registration.ts
const MAX_PASSWORD_BYTES = 72;

/** Sets a new password for a reset token. Redirects to /login on success. */
export function ResetPasswordForm({ token }: { token: string }) {
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [tokenInvalid, setTokenInvalid] = useState(false);

  const passwordBytes = new TextEncoder().encode(password).length;
  const passwordTooShort = password.length > 0 && password.length < MIN_PASSWORD_LENGTH;
  const passwordTooLong = passwordBytes > MAX_PASSWORD_BYTES;
  const passwordsMismatch = confirmPassword.length > 0 && password !== confirmPassword;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (loading) return;
    setError(null);
    if (password.length < MIN_PASSWORD_LENGTH) {
      setError(`Password must be at least ${MIN_PASSWORD_LENGTH} characters.`);
      return;
    }
    if (passwordTooLong) {
      setError("Password is too long.");
      return;
    }
    if (password !== confirmPassword) {
      setError("Passwords do not match.");
      return;
    }
    setLoading(true);
    try {
      const res = await fetch("/api/auth/reset-password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token, password }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setError((data as { error?: string }).error ?? "This reset link is invalid or has expired.");
        setTokenInvalid(true);
        setLoading(false);
        return;
      }
      window.location.href = "/login?reset=1";
    } catch {
      setLoading(false);
      setError("Something went wrong. Please try again.");
    }
  }

  if (tokenInvalid) {
    return (
      <div className="flex flex-col items-center gap-3 text-center">
        <p role="alert" className="text-sm text-red-400">
          {error}
        </p>
        <a
          href="/forgot-password"
          className="text-xs text-emerald-400 transition-colors hover:text-emerald-300"
        >
          Request a new reset link
        </a>
      </div>
    );
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-4 w-full max-w-sm" noValidate>
      <div className="flex flex-col gap-1">
        <label className="text-sm text-zinc-300" htmlFor="new-password">
          New password
        </label>
        <input
          id="new-password"
          type="password"
          autoComplete="new-password"
          required
          minLength={MIN_PASSWORD_LENGTH}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          aria-invalid={passwordTooShort || passwordTooLong}
          aria-describedby="new-password-hint"
          className="rounded-md bg-zinc-800 border border-zinc-700 px-3 py-2 text-sm text-zinc-100 placeholder-zinc-500 focus:outline-none focus:ring-1 focus:ring-emerald-500"
        />
        <p
          id="new-password-hint"
          className={`text-[11px] ${passwordTooLong ? "text-red-400" : "text-zinc-500"}`}
        >
          {MIN_PASSWORD_LENGTH}-{MAX_PASSWORD_BYTES} characters.
        </p>
      </div>
      <div className="flex flex-col gap-1">
        <label className="text-sm text-zinc-300" htmlFor="confirm-password">
          Confirm password
        </label>
        <input
          id="confirm-password"
          type="password"
          autoComplete="new-password"
          required
          value={confirmPassword}
          onChange={(e) => setConfirmPassword(e.target.value)}
          aria-invalid={passwordsMismatch}
          aria-describedby={passwordsMismatch ? "confirm-password-error" : undefined}
          className="rounded-md bg-zinc-800 border border-zinc-700 px-3 py-2 text-sm text-zinc-100 placeholder-zinc-500 focus:outline-none focus:ring-1 focus:ring-emerald-500"
        />
        {passwordsMismatch && (
          <p id="confirm-password-error" className="text-[11px] text-red-400">
            Passwords do not match.
          </p>
        )}
      </div>
      {error && (
        <p role="alert" className="text-sm text-red-400">
          {error}
        </p>
      )}
      <button
        type="submit"
        disabled={loading}
        className="rounded-md bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 px-4 py-2 text-sm font-medium text-white transition-colors"
      >
        {loading ? "Resetting…" : "Reset password"}
      </button>
    </form>
  );
}
