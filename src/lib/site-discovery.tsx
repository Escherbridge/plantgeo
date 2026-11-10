import type { Metadata } from "next";
import { OPERATOR_LEGAL_NAME } from "@/lib/site-operator";

/**
 * Public origin for discovery tags. Relative metadata URLs resolve against
 * this host, including when the request arrives on another domain.
 */
export const SITE_ORIGIN = "https://plantgeo.aevani.com";

export const SITE_NAME = "PlantGeo";

/**
 * Next resolves a `./` canonical and og:url against the current route
 * pathname, then against metadataBase. Each public page therefore names
 * itself on the public origin.
 */
const SELF_PATH = "./";

export const siteDiscoveryMetadata = {
  metadataBase: new URL(SITE_ORIGIN),
  alternates: { canonical: SELF_PATH },
  openGraph: { url: SELF_PATH },
} as const satisfies Pick<Metadata, "metadataBase" | "alternates" | "openGraph">;

/** Operator from site-operator.ts, and this site on the public origin. */
export const siteDiscoveryGraph = {
  "@context": "https://schema.org",
  "@graph": [
    {
      "@type": "Organization",
      "@id": `${SITE_ORIGIN}/#organization`,
      name: OPERATOR_LEGAL_NAME,
      url: SITE_ORIGIN,
    },
    {
      "@type": "WebSite",
      "@id": `${SITE_ORIGIN}/#website`,
      name: SITE_NAME,
      url: SITE_ORIGIN,
      publisher: { "@id": `${SITE_ORIGIN}/#organization` },
    },
  ],
} as const;

export function SiteDiscoveryJsonLd() {
  return (
    <script
      type="application/ld+json"
      dangerouslySetInnerHTML={{ __html: JSON.stringify(siteDiscoveryGraph) }}
    />
  );
}
