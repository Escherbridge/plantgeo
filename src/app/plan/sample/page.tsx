import type { Metadata } from "next";
import { RememberPlanRef } from "@/app/plan/RememberPlanRef";
import { loadCatalog, loadSamplePlan, toWorkspacePlan } from "@/app/plan/load-plan";
import { PlanWorkspace } from "@/components/plans/PlanWorkspace";
import { normalizeRefCode } from "@/lib/plans/ref-code";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Sample plan — not a real customer - PlantGeo",
  description:
    "A demonstration site plan on real Ada County, Idaho data. Not a real customer.",
  alternates: { canonical: "/plan/sample" },
};

export default async function SamplePlanPage({
  searchParams,
}: {
  searchParams: Promise<{ ref?: string | string[] }>;
}) {
  const query = await searchParams;
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
