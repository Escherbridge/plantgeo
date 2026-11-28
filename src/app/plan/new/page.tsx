import Link from "next/link";
import { DropPlanRef } from "@/app/plan/DropPlanRef";
import { RememberPlanRef } from "@/app/plan/RememberPlanRef";
import { NewPlanForm } from "@/app/plan/new/NewPlanForm";
import { PlanInviteLine } from "@/app/plan/new/PlanInviteLine";
import { DocumentPage } from "@/components/layout/DocumentPage";
import { EditorialLink, EditorialProse, EditorialSection } from "@/components/ui/editorial";
import { publicPageMetadata } from "@/lib/public-metadata";
import { resolvePlanInvite } from "@/lib/server/affiliate/review";
import { db } from "@/lib/server/db";

const TITLE = "Start a site plan - PlantGeo";
const DESCRIPTION =
  "Start a site plan on PlantGeo's live map. A signed-in plan is saved to your account. A guest plan is saved to a private link.";

export const metadata = publicPageMetadata({
  title: TITLE,
  description: DESCRIPTION,
  path: "/plan/new",
});

export const dynamic = "force-dynamic";

export default async function NewPlanPage({
  searchParams,
}: {
  searchParams: Promise<{ ref?: string | string[] }>;
}) {
  const query = await searchParams;
  const rawRef = Array.isArray(query.ref) ? query.ref[0] : query.ref;
  const invite = await resolvePlanInvite(db, rawRef);

  return (
    <DocumentPage
      jsonLd={{ name: TITLE, description: DESCRIPTION, path: "/plan/new" }}
      eyebrow="Site plan"
      title="Start a plan on the live map."
      lead="The plan opens on the same demonstration area as the sample. Catalog picks are planning references."
    >
      <EditorialSection index="01" title="Your plan" id="start">
        {invite.refCode ? <RememberPlanRef refCode={invite.refCode} /> : null}
        {invite.dropStoredCode ? <DropPlanRef /> : null}
        <PlanInviteLine line={invite.line} />
        <NewPlanForm refCode={invite.dropStoredCode ? "" : invite.refCode ?? undefined} />
        <EditorialProse className="mt-comfortable">
          <p>
            If you are signed in, the plan is saved to your account. If you are not, the next page
            is a private link. Keep that link; it is how you edit the plan.{" "}
            <Link href="/login?callbackUrl=%2Fplan%2Fnew" className="underline decoration-accent decoration-2 underline-offset-4">
              Sign in
            </Link>{" "}
            or{" "}
            <Link href="/register?callbackUrl=%2Fplan%2Fnew" className="underline decoration-accent decoration-2 underline-offset-4">
              create an account
            </Link>{" "}
            first if you want the plan on an account. See the{" "}
            <EditorialLink href="/plan/sample?ref=ada-demo">sample plan</EditorialLink>.
          </p>
        </EditorialProse>
      </EditorialSection>
    </DocumentPage>
  );
}
