import Link from "next/link";
import type { Metadata } from "next";
import { ForgotPasswordForm } from "@/components/auth/ForgotPasswordForm";

export const metadata: Metadata = { title: "Forgot password — PlantGeo" };

export default function ForgotPasswordPage() {
  return (
    <div className="flex flex-col gap-6">
      <div className="text-center">
        <h2 className="text-xl text-zinc-100 [font-family:var(--font-auth-display)]">
          Forgot your password?
        </h2>
        <p className="mt-1 text-xs text-zinc-500">We&apos;ll email you a link to reset it.</p>
      </div>
      <ForgotPasswordForm />
      <p className="text-center text-xs text-zinc-500">
        <Link href="/login" className="text-emerald-400 transition-colors hover:text-emerald-300">
          Back to sign in
        </Link>
      </p>
    </div>
  );
}
