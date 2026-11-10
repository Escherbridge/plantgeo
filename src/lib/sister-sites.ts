import { SITE_ORIGIN } from "@/lib/public-site";

/**
 * Sister sites for the footer block. Anchors are the descriptive phrases from
 * the PlantGeo copy. PlantGeo itself is omitted; each href is that site's own
 * canonical origin.
 */
export const SISTER_SITES = [
  {
    href: "https://primusneo.com",
    label: "PrimusNeo, the technology innovation lab",
  },
  {
    href: "https://neos.primusneo.com",
    label: "NEOS, a workspace for agreements and decisions",
  },
  {
    href: "https://azoa.ardanova.com",
    label: "AZOA, an engine for structured workflows",
  },
  {
    href: "https://ardanova.com",
    label: "ArdaNova, a workspace for shared work",
  },
  {
    href: "https://aevani.com",
    label: "Aevani, a marketplace for growing tools",
  },
  {
    href: "https://overseasgenai.com",
    label: "OverseasGenAI, a venture shipyard",
  },
  {
    href: "https://glitxz.com",
    label: "Glitxz, an artist-led market for work in progress",
  },
] as const;

export function organizationJsonLd(): Record<string, unknown> {
  return {
    "@context": "https://schema.org",
    "@type": "Organization",
    name: "PlantGeo",
    url: SITE_ORIGIN,
    sameAs: SISTER_SITES.map((site) => site.href),
  };
}
