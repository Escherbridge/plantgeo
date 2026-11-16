import type { Metadata } from "next";
import { RSS_ALTERNATE } from "@/lib/content/rss";
import { SITE_ORIGIN } from "@/lib/public-site";

export const OG_IMAGE = {
  url: `${SITE_ORIGIN}/og.png`,
  width: 1200,
  height: 630,
  alt: "PlantGeo",
} as const;

export const PUBLIC_AUTHOR = "Escherbridge";

/** Indexable page metadata: this page's title, description, canonical, and social tags. */
export function publicPageMetadata(input: {
  title: string;
  description: string;
  path: string;
}): Metadata {
  return {
    title: input.title,
    description: input.description,
    authors: [{ name: PUBLIC_AUTHOR }],
    robots: { index: true, follow: true },
    alternates: { canonical: input.path, types: RSS_ALTERNATE },
    openGraph: {
      type: "website",
      url: input.path,
      title: input.title,
      description: input.description,
      siteName: "PlantGeo",
      locale: "en_US",
      images: [OG_IMAGE],
    },
    twitter: {
      card: "summary_large_image",
      title: input.title,
      description: input.description,
      images: [OG_IMAGE.url],
    },
  };
}
