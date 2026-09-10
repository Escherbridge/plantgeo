import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { getServerSession } from "@/lib/server/auth";
import { ModerationPanel } from "@/components/panels/ModerationPanel";
import { ContributionQueue } from "@/components/panels/ContributionQueue";

export const metadata: Metadata = {
  title: "Moderation - PlantGeo",
  description: "Review community-submitted intervention recommendations before they publish to the map.",
};

const MODERATION_ROLES = ["expert", "admin"];

export default async function ModerationPage() {
  const session = await getServerSession();
  const role = (session?.user as { platformRole?: string } | undefined)?.platformRole;

  if (!role || !MODERATION_ROLES.includes(role)) {
    redirect("/");
    return null;
  }

  return (
    <div className="viewport-below-top-bar overflow-y-auto bg-zinc-950 px-6 py-8">
      <div className="mx-auto max-w-4xl space-y-6">
        <section aria-labelledby="community-review-title" className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-4 shadow-2xl">
          <h1 id="community-review-title" className="text-lg font-semibold text-zinc-100">Community recommendations</h1>
          <p className="mt-1 mb-3 text-sm text-zinc-400">Review submitted sites and approve suitable recommendations for the public map.</p>
          <ContributionQueue />
        </section>
        <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 shadow-2xl">
          <ModerationPanel />
        </div>
      </div>
    </div>
  );
}
