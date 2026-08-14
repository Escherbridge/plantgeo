import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { getServerSession } from "@/lib/server/auth";

export const metadata: Metadata = {
  title: "Platform Admin - PlantGeo",
  description: "Platform administration for the durable job runner.",
};

/**
 * Server-side role gate for everything under /admin, mirroring how /moderation gates by
 * `platformRole` (src/app/moderation/page.tsx). Before 2026-08-14 this segment had no gate at all:
 * /admin/jobs was a public URL, and only the tRPC procedures behind it were role-checked — so an
 * anonymous visitor loaded the whole admin console and saw it fail one query at a time.
 *
 * The tRPC `adminProcedure` checks stay as they are. This gate hides the surface; that one is the
 * authorization boundary, and neither is a substitute for the other.
 */
export default async function AdminLayout({ children }: { children: React.ReactNode }) {
  const session = await getServerSession();
  const role = (session?.user as { platformRole?: string } | undefined)?.platformRole;

  if (role !== "admin") {
    redirect("/");
  }

  return <div className="min-h-screen bg-slate-950">{children}</div>;
}
