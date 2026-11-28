import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { AffiliateInvitePanel } from "@/app/dashboard/affiliate/AffiliateInvitePanel";
import { TOUCH_TARGET } from "@/components/ui/touch-target";
import { loadAffiliateDashboard } from "@/lib/server/affiliate/review";
import { getServerSession } from "@/lib/server/auth";
import { db } from "@/lib/server/db";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Your invite link - PlantGeo",
  description:
    "The link an approved affiliate sends when inviting someone to plan a site, and the count of plans started from it.",
};

export default async function AffiliateDashboardPage() {
  const session = await getServerSession();
  const userId = session?.user?.id;
  if (!userId) redirect("/login");

  const dashboard = await loadAffiliateDashboard(db, userId);
  if (!dashboard) redirect("/dashboard");

  return (
    <div className="viewport-below-top-bar flex flex-col bg-zinc-950">
      <header className="shrink-0 border-b border-zinc-800 px-6 py-4">
        <Link
          href="/dashboard"
          className={`inline-flex items-center gap-1.5 text-sm text-zinc-500 transition-colors hover:text-zinc-300 ${TOUCH_TARGET}`}
        >
          <ArrowLeft className="h-4 w-4" aria-hidden />
          Dashboard
        </Link>
      </header>
      <main className="mx-auto min-h-0 w-full max-w-3xl flex-1 overflow-y-auto px-6 py-8">
        <AffiliateInvitePanel
          inviteUrl={dashboard.inviteUrl}
          plansStarted={dashboard.plansStarted}
        />
      </main>
    </div>
  );
}
