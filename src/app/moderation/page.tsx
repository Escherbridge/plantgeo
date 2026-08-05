import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { getServerSession } from "@/lib/server/auth";
import { ContributionQueue } from "@/components/panels/ContributionQueue";

export const metadata: Metadata = {
  title: "Moderation - PlantGeo",
  description: "Review community-submitted intervention recommendations before they publish to the map.",
};

/** Roles that may reach the moderation queue; mirrors expertProcedure at src/lib/server/trpc/init.ts. */
const MODERATION_ROLES = ["expert", "admin"];

/**
 * Dedicated route for the moderation queue rather than a seventh map panel — see
 * src/components/panels/AGENTS.md. Redirects before rendering anything so a
 * non-expert visiting this URL directly gets no shell at all, not a broken one.
 * This is presentation only; contributionsRouter's expertProcedure is the
 * authoritative gate and is re-checked on every query and mutation the queue makes.
 */
export default async function ModerationPage() {
  const session = await getServerSession();
  const role = (session?.user as { platformRole?: string } | undefined)?.platformRole;

  if (!role || !MODERATION_ROLES.includes(role)) {
    redirect("/");
    return null;
  }

  return (
    <div className="min-h-full bg-zinc-950 px-6 py-8 [font-family:var(--font-onboarding-mono,inherit)]">
      <div className="mx-auto max-w-2xl">
        <h1 className="text-xl font-semibold text-zinc-100">Moderation queue</h1>
        <p className="mt-1 text-sm text-zinc-400">
          Community intervention recommendations wait here in{" "}
          <code className="text-zinc-300">pending_review</code> until an expert
          publishes or rejects them. Publishing is what makes a submission
          visible on the map.
        </p>

        <div className="mt-6 rounded-xl border border-zinc-800 bg-zinc-900/50">
          <ContributionQueue />
        </div>
      </div>
    </div>
  );
}
