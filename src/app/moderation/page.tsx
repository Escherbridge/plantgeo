import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { getServerSession } from "@/lib/server/auth";
import { ModerationPanel } from "@/components/panels/ModerationPanel";

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
    <div className="min-h-full bg-zinc-950 px-6 py-8">
      <div className="mx-auto max-w-4xl">
        <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 shadow-2xl">
          <ModerationPanel />
        </div>
      </div>
    </div>
  );
}
