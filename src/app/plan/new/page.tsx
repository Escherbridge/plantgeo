import type { Metadata } from "next";
import Link from "next/link";
import { NewPlanForm } from "@/app/plan/new/NewPlanForm";
import { DocumentPage } from "@/components/layout/DocumentPage";
import { EditorialLink, EditorialProse, EditorialSection } from "@/components/ui/editorial";

export const metadata: Metadata = {
  title: "Start a site plan - PlantGeo",
  description:
    "Start a site plan on PlantGeo's live map. A signed-in plan is saved to your account. A guest plan is saved to a private link.",
};

export default function NewPlanPage() {
  return (
    <DocumentPage
      eyebrow="Site plan"
      title="Start a plan on the live map."
      lead="The plan opens on the same demonstration area as the sample. Catalog picks are planning references."
    >
      <EditorialSection index="01" title="Your plan" id="start">
        <NewPlanForm />
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
