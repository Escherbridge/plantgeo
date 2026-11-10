import type { Metadata } from "next";
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
    },
  };
}
