import type { Metadata } from "next";
import { OG_IMAGE } from "@/lib/public-metadata";
import type { Essay } from "@/lib/writing/essays";
import { blogPath } from "@/lib/writing/essays";

export const ESSAY_AUTHOR = "Escherbridge";

export function essayPageMetadata(essay: Essay): Metadata {
  const canonical = blogPath(essay);
  const title = `${essay.title} — PlantGeo`;
  return {
    title,
    description: essay.summary,
    authors: [{ name: ESSAY_AUTHOR }],
    alternates: { canonical },
    openGraph: {
      type: "article",
      url: canonical,
      title,
      description: essay.summary,
      publishedTime: essay.date,
      images: [OG_IMAGE],
    },
    twitter: {
      card: "summary_large_image",
      title,
      description: essay.summary,
      images: [OG_IMAGE.url],
    },
  };
}
