"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { DEFAULT_CALLBACK_URL, safeCallbackUrl } from "@/lib/auth/callback-url";
import { MAX_BCRYPT_PASSWORD_BYTES, MIN_PASSWORD_LENGTH, registrationSchema } from "@/lib/auth/registration";

/** Registration form; redirects to /login with callbackUrl preserved on success. */
export function RegisterForm({
  callbackUrl = DEFAULT_CALLBACK_URL,
}: {
  callbackUrl?: string;
}) {
  const router = useRouter();
  const destination = safeCallbackUrl(callbackUrl);
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const passwordBytes = new TextEncoder().encode(password).length;
  const passwordTooShort = password.length > 0 && password.length < MIN_PASSWORD_LENGTH;
  const passwordTooLong = passwordBytes > MAX_BCRYPT_PASSWORD_BYTES;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (loading) return;
    setError(null);
    const parsed = registrationSchema.safeParse({ name, email, password });
    if (!parsed.success) {
      setError(parsed.error.issues[0]?.message ?? "Check your registration details.");
      return;
    }
    setLoading(true);
    try {
      const res = await fetch("/api/auth/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(parsed.data),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setError((data as { error?: string }).error ?? "Registration failed. Please try again.");
        return;
      }
      const query = new URLSearchParams({ registered: "1" });
      if (destination !== DEFAULT_CALLBACK_URL) {
        query.set("callbackUrl", destination);
      }
      router.push(`/login?${query.toString()}`);
    } catch {
      setError("Could not connect to create your account. Check your connection and try again.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-4 w-full max-w-sm" noValidate>
      <div className="flex flex-col gap-1">
        <label className="text-sm text-zinc-300" htmlFor="name">
          Name (optional)
        </label>
        <input
          id="name"
          type="text"
          autoComplete="name"
          maxLength={100}
          value={name}
          onChange={(e) => setName(e.target.value)}
          className="rounded-md bg-zinc-800 border border-zinc-700 px-3 py-2 text-sm text-zinc-100 placeholder-zinc-500 focus:outline-none focus:ring-1 focus:ring-emerald-500"
        />
      </div>
      <div className="flex flex-col gap-1">
        <label className="text-sm text-zinc-300" htmlFor="reg-email">
          Email
        </label>
        <input
          id="reg-email"
          type="email"
          autoComplete="email"
          maxLength={254}
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          aria-invalid={Boolean(error)}
          aria-describedby={error ? "reg-error" : undefined}
          className="rounded-md bg-zinc-800 border border-zinc-700 px-3 py-2 text-sm text-zinc-100 placeholder-zinc-500 focus:outline-none focus:ring-1 focus:ring-emerald-500"
        />
      </div>
      <div className="flex flex-col gap-1">
        <label className="text-sm text-zinc-300" htmlFor="reg-password">
          Password
        </label>
        <input
          id="reg-password"
          type="password"
          autoComplete="new-password"
          required
          minLength={MIN_PASSWORD_LENGTH}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          aria-invalid={passwordTooShort || passwordTooLong}
          aria-describedby="reg-password-hint"
          className="rounded-md bg-zinc-800 border border-zinc-700 px-3 py-2 text-sm text-zinc-100 placeholder-zinc-500 focus:outline-none focus:ring-1 focus:ring-emerald-500"
        />
        <p
          id="reg-password-hint"
          className={`text-[11px] ${passwordTooLong ? "text-red-400" : "text-zinc-500"}`}
        >
          At least {MIN_PASSWORD_LENGTH} characters, up to {MAX_BCRYPT_PASSWORD_BYTES} bytes.
        </p>
      </div>
      {error && (
        <p id="reg-error" role="alert" className="text-sm text-red-400">
          {error}
        </p>
      )}
      <button
        type="submit"
        disabled={loading}
        className="rounded-md bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 px-4 py-2 text-sm font-medium text-white transition-colors"
      >
        {loading ? "Creating account…" : "Create account"}
      </button>
    </form>
  );
}
