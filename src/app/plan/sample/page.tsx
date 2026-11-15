import { RememberPlanRef } from "@/app/plan/RememberPlanRef";
import { publicPageMetadata } from "@/lib/public-metadata";
import { loadCatalog, loadSamplePlan, toWorkspacePlan } from "@/app/plan/load-plan";
import { PlanWorkspace } from "@/components/plans/PlanWorkspace";
import { normalizeRefCode } from "@/lib/plans/ref-code";

export const dynamic = "force-dynamic";

export const metadata = publicPageMetadata({
  title: "Ada County Sample Plan — PlantGeo",
  description:
    "Explore elevation, drainage paths, wetlands, soils, and flood boundaries on a real Ada County, Idaho demonstration parcel, then start your own site plan.",
  path: "/plan/sample",
});

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
