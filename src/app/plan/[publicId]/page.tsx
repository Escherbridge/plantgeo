import type { Metadata } from "next";
import { loadCatalog, loadPlanByPublicId, toWorkspacePlan } from "@/app/plan/load-plan";
import { PlanWorkspace } from "@/components/plans/PlanWorkspace";
import { SAMPLE_PLAN_LABEL } from "@/lib/plans/sample";

export const dynamic = "force-dynamic";

const GUEST_TOKEN = /^[A-Za-z0-9_-]{16,512}$/;

export async function generateMetadata({
  params,
}: {
  params: Promise<{ publicId: string }>;
}): Promise<Metadata> {
  const { publicId } = await params;
  const title = publicId === "sample-ada-county" ? SAMPLE_PLAN_LABEL : "Site plan";
  return {
    title: `${title} - PlantGeo`,
    description: "A PlantGeo site plan on live map data.",
    robots: { index: false, follow: false },
    referrer: "no-referrer",
  };
}

export default async function PlanPage({
  params,
  searchParams,
}: {
  params: Promise<{ publicId: string }>;
  searchParams: Promise<{ token?: string | string[] }>;
}) {
  const { publicId } = await params;
  const query = await searchParams;
  const rawToken = Array.isArray(query.token) ? query.token[0] : query.token;
  const guestToken = rawToken && GUEST_TOKEN.test(rawToken) ? rawToken : undefined;
  const [plan, catalog] = await Promise.all([
    loadPlanByPublicId(publicId, guestToken),
    loadCatalog(),
  ]);

  return (
    <PlanWorkspace
      plan={toWorkspacePlan(plan, false)}
      catalog={catalog}
      guestToken={guestToken ?? null}
      refCode={null}
      showStart={false}
    />
  );
}
