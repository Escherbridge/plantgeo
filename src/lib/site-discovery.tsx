import type { Metadata } from "next";
import { SITE_ORIGIN } from "@/lib/public-site";
import { SISTER_SITES } from "@/lib/sister-sites";
import { OPERATOR_EMAIL, OPERATOR_LEGAL_NAME, OPERATOR_POSTAL_ADDRESS } from "@/lib/site-operator";

export const SITE_NAME = "PlantGeo";

/**
 * Next resolves a `./` canonical and og:url against the current route
 * pathname, then against metadataBase. Each public page therefore names
 * itself on the public origin. Pages that set their own canonical keep it.
 */
const SELF_PATH = "./";

export const siteDiscoveryMetadata = {
  metadataBase: new URL(SITE_ORIGIN),
  alternates: {
    canonical: SELF_PATH,
    types: { "application/rss+xml": "/feed.xml" },
  },
  openGraph: { url: SELF_PATH },
} as const satisfies Pick<Metadata, "metadataBase" | "alternates" | "openGraph">;

/**
 * One discovery graph. The operator and the site are separate nodes.
 * Sister origins stay on the organization as sameAs, the list already
 * published for the footer, rather than a second Organization script.
 */
export const siteDiscoveryGraph = {
  "@context": "https://schema.org",
  "@graph": [
    {
      "@type": "Organization",
      "@id": `${SITE_ORIGIN}/#organization`,
      name: SITE_NAME,
      legalName: OPERATOR_LEGAL_NAME,
      url: SITE_ORIGIN,
      email: OPERATOR_EMAIL,
      logo: `${SITE_ORIGIN}/icons/icon-192.png`,
      address: OPERATOR_POSTAL_ADDRESS,
      sameAs: SISTER_SITES.map((site) => site.href),
    },
    {
      "@type": "WebSite",
      "@id": `${SITE_ORIGIN}/#website`,
      name: SITE_NAME,
      url: SITE_ORIGIN,
      inLanguage: "en-US",
      publisher: { "@id": `${SITE_ORIGIN}/#organization` },
    },
  ],
};

export function SiteDiscoveryJsonLd() {
  const json = JSON.stringify(siteDiscoveryGraph).replace(/</g, "\\u003c");
  return (
    <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: json }} />
  );
}
