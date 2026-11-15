import type { Metadata } from "next";
import { RememberPlanRef } from "@/app/plan/RememberPlanRef";
import {
  loadCatalog,
  loadPlanByPublicId,
  loadSamplePlan,
  toWorkspacePlan,
} from "@/app/plan/load-plan";
import { PlanWorkspace } from "@/components/plans/PlanWorkspace";
import { normalizeRefCode } from "@/lib/plans/ref-code";
import { SAMPLE_PLAN_PUBLIC_ID } from "@/lib/plans/sample";

export const dynamic = "force-dynamic";

const GUEST_TOKEN = /^[A-Za-z0-9_-]{16,512}$/;

export async function generateMetadata({
  params,
}: {
  params: Promise<{ publicId: string }>;
}): Promise<Metadata> {
  const { publicId } = await params;
  if (publicId === SAMPLE_PLAN_PUBLIC_ID) {
    return {
      title: "Ada County Sample Plan — PlantGeo",
      description:
        "Inspect elevation, drainage paths, wetlands, soils, and flood boundaries on a real Ada County, Idaho sample parcel, then start your own site plan.",
      alternates: { canonical: `/plan/${SAMPLE_PLAN_PUBLIC_ID}` },
    };
  }
  return {
    title: "Site plan — PlantGeo",
    description: "A private PlantGeo site plan on live public map layers.",
    robots: { index: false, follow: false },
    referrer: "no-referrer",
  };
}

export default async function PlanPage({
  params,
  searchParams,
}: {
  params: Promise<{ publicId: string }>;
  searchParams: Promise<{ token?: string | string[]; ref?: string | string[] }>;
}) {
  const { publicId } = await params;
  const query = await searchParams;
  const isSample = publicId === SAMPLE_PLAN_PUBLIC_ID;

  if (isSample) {
    const rawRef = Array.isArray(query.ref) ? query.ref[0] : query.ref;
    const refCode = normalizeRefCode(rawRef);
    const [sample, catalog] = await Promise.all([loadSamplePlan(), loadCatalog()]);

    return (
      <>
        {refCode ? <RememberPlanRef refCode={refCode} /> : null}
        <PlanWorkspace
          plan={toWorkspacePlan(sample, sample === null)}
          catalog={catalog}
          guestToken={null}
          refCode={refCode}
          showStart
        />
      </>
    );
  }

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
