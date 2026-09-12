import type { Metadata } from "next";
import Link from "next/link";
import { getServerSession } from "@/lib/server/auth";
import { ContributionQueue } from "@/components/panels/ContributionQueue";

export const metadata: Metadata = {
  title: "Moderation - PlantGeo",
  description: "Review community-submitted intervention recommendations before they publish to the map.",
};

const MODERATION_ROLES = ["expert", "admin"];

export default async function ModerationPage() {
  let session: Awaited<ReturnType<typeof getServerSession>>;
  try {
    session = await getServerSession();
  } catch {
    return <p role="alert" className="p-8 text-zinc-200">The sign-in service is unavailable. Community review could not be loaded. Please try again.</p>;
  }
  const role = (session?.user as { platformRole?: string } | undefined)?.platformRole;

  if (!session || !role || !MODERATION_ROLES.includes(role)) {
    return (
      <div className="p-8 text-zinc-200">
        <h1 className="text-lg font-semibold">Community review</h1>
        <p className="mt-2">{!session ? "Sign in to review community recommendations." : "Only experts and administrators can review community recommendations."}</p>
        <Link className="mt-3 inline-block underline" href={!session ? "/api/auth/signin?callbackUrl=%2Fmoderation" : "/"}>{!session ? "Sign in" : "Return to map"}</Link>
      </div>
    );
  }

  return (
    <div className="viewport-below-top-bar overflow-y-auto bg-zinc-950 px-6 py-8">
      <div className="mx-auto max-w-4xl space-y-6">
        <section aria-labelledby="community-review-title" className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-4 shadow-2xl">
          <h1 id="community-review-title" className="text-lg font-semibold text-zinc-100">Community recommendations</h1>
          <p className="mt-1 mb-3 text-sm text-zinc-400">Review submitted sites and approve suitable recommendations for the public map.</p>
          <ContributionQueue />
        </section>
      </div>
    </div>
  );
}
