import type { Metadata } from "next";
import { AffiliateApplicationForm } from "@/app/affiliate/AffiliateApplicationForm";
import { DocumentPage } from "@/components/layout/DocumentPage";
import { EditorialLink, EditorialProse, EditorialSection } from "@/components/ui/editorial";

export const metadata: Metadata = {
  title: "Become a PlantGeo affiliate - PlantGeo",
  description:
    "Apply to invite people to plan sites on PlantGeo's live map. Terms will be shared on review.",
  alternates: { canonical: "/affiliate" },
};

export default function AffiliatePage() {
  return (
    <DocumentPage
      eyebrow="Affiliates"
      title="Become a PlantGeo affiliate."
      lead="Invite people to plan a site on the live map. Catalog picks from Aevani can be added to a plan. Applications are reviewed by hand."
    >
      <EditorialSection index="01" title="What this is" id="about">
        <EditorialProse>
          <p>
            An application asks to be reviewed. It does not grant access, and it does not issue a
            code. Terms will be shared on review. The{" "}
            <EditorialLink href="/plan/sample?ref=ada-demo">sample plan</EditorialLink> is a
            demonstration on real map data, and it is not a customer.
          </p>
        </EditorialProse>
      </EditorialSection>
      <EditorialSection index="02" title="Application" id="apply">
        <AffiliateApplicationForm />
      </EditorialSection>
    </DocumentPage>
  );
}
