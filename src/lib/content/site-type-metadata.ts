import type { Metadata } from "next";
import { RSS_ALTERNATE } from "@/lib/content/rss";
import { OG_IMAGE } from "@/lib/public-metadata";
import {
  SITE_TYPE_AUTHOR,
  siteTypePath,
  type SiteTypeArticle,
} from "@/lib/content/site-types";

export function siteTypeArticleMetadata(page: SiteTypeArticle): Metadata {
  const canonical = siteTypePath(page);
  return {
    title: page.seoTitle,
    description: page.metaDescription,
    authors: [{ name: SITE_TYPE_AUTHOR }],
    alternates: { canonical, types: RSS_ALTERNATE },
    openGraph: {
      type: "article",
      url: canonical,
      title: page.seoTitle,
      description: page.metaDescription,
      publishedTime: page.publishedAt,
      modifiedTime: page.lastReviewed,
      images: [OG_IMAGE],
    },
    twitter: {
      card: "summary_large_image",
      title: page.seoTitle,
      description: page.metaDescription,
      images: [OG_IMAGE.url],
    },
  };
}
