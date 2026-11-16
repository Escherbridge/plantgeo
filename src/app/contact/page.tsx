import { DocumentPage } from "@/components/layout/DocumentPage";
import { publicPageMetadata } from "@/lib/public-metadata";
import {
  EditorialLink,
  EditorialProse,
  EditorialSection,
} from "@/components/ui/editorial";
import {
  OPERATOR_CONTACT_NAME,
  OPERATOR_EMAIL,
  OPERATOR_LEGAL_NAME,
  OPERATOR_LOCATION,
  OPERATOR_PHONE_DISPLAY,
  OPERATOR_PHONE_TEL,
} from "@/lib/site-operator";

const TITLE = "Contact - PlantGeo";
const DESCRIPTION = `${OPERATOR_LEGAL_NAME} operates PlantGeo from ${OPERATOR_LOCATION}. Contact ${OPERATOR_CONTACT_NAME} at ${OPERATOR_EMAIL} or ${OPERATOR_PHONE_DISPLAY}. Account mail is sent to an address already on the account.`;

export const metadata = publicPageMetadata({
  title: TITLE,
  description: DESCRIPTION,
  path: "/contact",
});

export default function ContactPage() {
  return (
    <DocumentPage
      jsonLd={{ name: TITLE, description: DESCRIPTION, path: "/contact" }}
      eyebrow="Contact"
      title="Reach the operator."
      lead={`${OPERATOR_LEGAL_NAME} operates this site. This page does not collect a message.`}
    >
      <EditorialSection index="01" title="Public contact" id="contact">
        <EditorialProse>
          <p>
            {OPERATOR_CONTACT_NAME} is the contact for {OPERATOR_LEGAL_NAME}.
          </p>
          <p>
            {OPERATOR_LOCATION}.{" "}
            <EditorialLink href={`mailto:${OPERATOR_EMAIL}`} prefetch={false}>
              {OPERATOR_EMAIL}
            </EditorialLink>
          </p>
          <p>
            Phone{" "}
            <EditorialLink href={`tel:${OPERATOR_PHONE_TEL}`} prefetch={false}>
              {OPERATOR_PHONE_DISPLAY}
            </EditorialLink>
            .
          </p>
        </EditorialProse>
      </EditorialSection>

      <EditorialSection index="02" title="Mail the site sends" id="mail">
        <EditorialProse>
          <p>
            Account mail runs outward, to an address already stored. When an
            email provider is configured, the application sends verification
            links, password-reset links, organization invitations, and
            environmental alerts. The provider is Resend or SendGrid, selected
            by the <em>EMAIL_PROVIDER</em> setting. Verification links last 24
            hours, reset links last one hour, and an organization invitation
            lasts seven days. Signing in with a password does not wait on the
            verification link.
          </p>
          <p>
            Environmental alerts are emailed when that subscription has email
            turned on. The message goes to the account address.
          </p>
        </EditorialProse>
      </EditorialSection>

      <EditorialSection index="03" title="Where a record lives" id="records">
        <EditorialProse>
          <p>
            A public strategy request is already on the map, with its location,
            title, and description. Comments on a record require a signed-in
            account. The{" "}
            <EditorialLink href="/about">About</EditorialLink> page names the
            stack and the upstream datasets.{" "}
            <EditorialLink href="/privacy">Privacy</EditorialLink> describes what
            is stored and which hosts receive a request.{" "}
            <EditorialLink href="/terms">Terms</EditorialLink> describes what
            publishing and the assistant do.
          </p>
        </EditorialProse>
      </EditorialSection>
    </DocumentPage>
  );
}
