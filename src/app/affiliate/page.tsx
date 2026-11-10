import type { Metadata } from "next";
import { AffiliateApplicationForm } from "@/app/affiliate/AffiliateApplicationForm";
import { DocumentPage } from "@/components/layout/DocumentPage";
import { EditorialLink, EditorialProse, EditorialSection } from "@/components/ui/editorial";

export const metadata: Metadata = {
  title: "Invite people to plan a site - PlantGeo",
  description:
    "Affiliates invite a landowner, a grower, a school, or a homestead to plan on the live map. Aevani catalogue items can be added to a plan later. Those items are not for sale yet. Applications are reviewed by hand.",
  alternates: { canonical: "/affiliate" },
};

export default function AffiliatePage() {
  return (
    <DocumentPage
      eyebrow="Affiliates"
      title="Invite people to plan a site."
      lead={
        <>
          Affiliates invite a landowner, a grower, a school, or a homestead to plan on the live
          map. <EditorialLink href="https://aevani.com">Aevani catalogue items</EditorialLink> can
          be added to a plan later. Those items are not for sale yet. Applications are reviewed by
          hand.
        </>
      }
    >
      <EditorialSection index="01" title="What this is" id="about">
        <EditorialProse>
          <p>
            An application asks to be reviewed. It does not grant access, and it does not issue a
            code. Terms will be shared on review. A referral share has not been set, so this page
            does not quote a rate or a payout date. The{" "}
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
