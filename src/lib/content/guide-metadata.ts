import type { Metadata } from "next";
import { GUIDE_AUTHOR, guidePath, type GuideArticle } from "@/lib/content/guides";
import { RSS_ALTERNATE } from "@/lib/content/rss";

export function guideArticleMetadata(page: GuideArticle): Metadata {
  const canonical = guidePath(page);
  return {
    title: page.seoTitle,
    description: page.metaDescription,
    authors: [{ name: GUIDE_AUTHOR }],
    alternates: { canonical, types: RSS_ALTERNATE },
    openGraph: {
      type: "article",
      url: canonical,
      title: page.seoTitle,
      description: page.metaDescription,
      publishedTime: page.publishedAt,
      modifiedTime: page.lastReviewed,
    },
    twitter: {
      card: "summary_large_image",
      title: page.seoTitle,
      description: page.metaDescription,
    },
  };
}
