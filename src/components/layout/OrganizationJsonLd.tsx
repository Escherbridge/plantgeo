import { organizationJsonLd } from "@/lib/sister-sites";

/** Site-wide Organization node. Sister origins are `sameAs`; canonical stays put. */
export function OrganizationJsonLd() {
  const json = JSON.stringify(organizationJsonLd()).replace(/</g, "\\u003c");
  return (
    <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: json }} />
  );
}
