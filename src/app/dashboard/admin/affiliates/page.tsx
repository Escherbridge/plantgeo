import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { AffiliateReviewScreen } from "@/app/dashboard/admin/affiliates/AffiliateReviewScreen";
import { TOUCH_TARGET } from "@/components/ui/touch-target";
import { getServerSession } from "@/lib/server/auth";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Affiliate applications - PlantGeo",
  description: "Approve or decline affiliate applications. Approval issues a code.",
};

export default async function AffiliateApplicationsPage() {
  const session = await getServerSession();
  const role = session?.user?.platformRole;
  if (role !== "admin") redirect("/");

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
        <h1 className="text-xl font-semibold text-zinc-50">Affiliate applications</h1>
        <p className="mt-2 mb-6 max-w-prose text-sm text-zinc-400">
          Approve an application to issue a code. Decline leaves the application without one.
        </p>
        <AffiliateReviewScreen />
      </main>
    </div>
  );
}
