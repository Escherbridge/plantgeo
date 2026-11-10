import type { Metadata } from "next";
import { DocumentPage } from "@/components/layout/DocumentPage";
import {
  EditorialLink,
  EditorialProse,
  EditorialSection,
} from "@/components/ui/editorial";
import { AI_GENERATED_DISCLAIMER } from "@/lib/regional-intelligence";

export const metadata: Metadata = {
  title: "Terms - PlantGeo",
  description:
    "How PlantGeo accounts, public requests, intervention review, and the assistant behave.",
};

export default function TermsPage() {
  return (
    <DocumentPage
      eyebrow="Terms"
      title="How the service behaves."
      lead="These are the rules the application enforces. What it stores, and which hosts receive a request, is on the Privacy page."
    >
      <EditorialSection index="01" title="Reading the map" id="map">
        <EditorialProse>
          <p>
            The map can be read without an account. Environmental layers show
            observations from the upstreams named on{" "}
            <EditorialLink href="/about">About</EditorialLink>. The application
            publishes no forecast: a day after today is answered as not forecast
            beyond today. Strategy recommendations are refused while validated
            strategy evidence is unpublished.
          </p>
          <p>
            Basemap lines and labels are OpenStreetMap, under the ODbL, compiled
            into a PMTiles archive. Satellite imagery is requested from Esri.
            Terrain images are requested from the public elevation bucket. A
            layer the deployment cannot serve is shown as unavailable.
          </p>
        </EditorialProse>
      </EditorialSection>

      <EditorialSection index="02" title="Accounts" id="accounts">
        <EditorialProse>
          <p>
            Creating an account takes an email and a password, or Google or
            GitHub when that provider is configured. The password is stored as
            a bcrypt hash. Later requests are attributed to the NextAuth session
            cookie. Signing out ends that session. Public routes, including
            registration and sign-in, are rate-limited.{" "}
            <EditorialLink href="/privacy">Privacy</EditorialLink> lists the
            fields an account holds.
          </p>
        </EditorialProse>
      </EditorialSection>

      <EditorialSection index="03" title="Publishing" id="publishing">
        <EditorialProse>
          <p>
            A strategy request publishes its location, title, and description
            immediately. There is no review queue for that kind of record.
            Anyone can read it. An intervention proposal is a separate
            submission: pending review is visible to signed-in readers, and
            approval makes it public. Comments and likes require a signed-in
            account.
          </p>
          <p>
            The author of a comment, or an expert or admin, can remove it. The
            row stays, with the deletion time and the account that removed it
            filled in. An evidence link attached to a community data submission
            stays limited to the submitter, their organization, or an expert or
            admin while the row is pending review.
          </p>
        </EditorialProse>
      </EditorialSection>

      <EditorialSection index="04" title="The assistant" id="assistant">
        <EditorialProse>
          <p>
            Analysis is sent after you choose an approximate coordinate or an
            exact one. The report carries this sentence: {AI_GENERATED_DISCLAIMER}{" "}
            A conversation and its messages are deleted after 30 days without
            an update. The land-context inquiry tool returns a draft string for
            you to copy. That procedure does not send email or submit a form.
          </p>
        </EditorialProse>
      </EditorialSection>
    </DocumentPage>
  );
}
