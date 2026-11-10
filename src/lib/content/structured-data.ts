import { absoluteSiteUrl, SITE_ORIGIN } from "@/lib/public-site";
import { OPERATOR_LEGAL_NAME } from "@/lib/site-operator";
import {
  SITE_TYPE_AUTHOR,
  siteTypePath,
  type SiteTypeArticle,
  type SiteTypeCitation,
} from "@/lib/content/site-types";
import { ESSAY_AUTHOR } from "@/lib/writing/essay-metadata";
import { blogPath, type Essay } from "@/lib/writing/essays";

export function essayStructuredData(essay: Essay) {
  const pageUrl = absoluteSiteUrl(blogPath(essay));
  return {
    "@context": "https://schema.org",
    "@graph": [
      {
        "@type": "Article",
        headline: essay.title,
        description: essay.summary,
        datePublished: essay.date,
        author: { "@type": "Organization", name: ESSAY_AUTHOR },
        publisher: { "@id": `${SITE_ORIGIN}/#organization` },
        mainEntityOfPage: pageUrl,
        image: `${SITE_ORIGIN}/og.png`,
      },
      {
        "@type": "BreadcrumbList",
        itemListElement: [
          { "@type": "ListItem", position: 1, name: "Home", item: absoluteSiteUrl("/") },
          { "@type": "ListItem", position: 2, name: "Blog", item: absoluteSiteUrl("/blog") },
          { "@type": "ListItem", position: 3, name: essay.title, item: pageUrl },
        ],
      },
    ],
  };
}

function siteTypeCitationData(source: SiteTypeCitation) {
  return {
    "@type": "CreativeWork" as const,
    name: source.name,
    url: source.url,
    ...(source.datePublished ? { datePublished: source.datePublished } : {}),
    ...(source.dateModified ? { dateModified: source.dateModified } : {}),
    ...(source.sameAs ? { sameAs: [...source.sameAs] } : {}),
  };
}

/** Article plus Home → Site types → child. Citations carry dates the draft states. */
export function siteTypeStructuredData(page: SiteTypeArticle) {
  const pageUrl = absoluteSiteUrl(siteTypePath(page));
  return {
    "@context": "https://schema.org",
    "@graph": [
      {
        "@type": "Article",
        headline: page.h1,
        description: page.metaDescription,
        datePublished: page.publishedAt,
        dateModified: page.lastReviewed,
        author: { "@type": "Organization", name: SITE_TYPE_AUTHOR },
        publisher: {
          "@type": "Organization",
          name: SITE_TYPE_AUTHOR,
          legalName: OPERATOR_LEGAL_NAME,
        },
        mainEntityOfPage: pageUrl,
        image: `${SITE_ORIGIN}/og.png`,
        citation: page.sources.map(siteTypeCitationData),
      },
      {
        "@type": "BreadcrumbList",
        itemListElement: [
          { "@type": "ListItem", position: 1, name: "Home", item: absoluteSiteUrl("/") },
          {
            "@type": "ListItem",
            position: 2,
            name: "Site types",
            item: absoluteSiteUrl("/site-types"),
          },
          { "@type": "ListItem", position: 3, name: page.crumb, item: pageUrl },
        ],
      },
    ],
  };
}

export function collectionStructuredData(input: {
  name: string;
  description: string;
  path: string;
  items: readonly { name: string; path: string }[];
}) {
  const pageUrl = absoluteSiteUrl(input.path);
  return {
    "@context": "https://schema.org",
    "@graph": [
      {
        "@type": "CollectionPage",
        name: input.name,
        description: input.description,
        url: pageUrl,
        mainEntity: {
          "@type": "ItemList",
          numberOfItems: input.items.length,
          itemListElement: input.items.map((item, index) => ({
            "@type": "ListItem",
            position: index + 1,
            name: item.name,
            url: absoluteSiteUrl(item.path),
          })),
        },
      },
      {
        "@type": "BreadcrumbList",
        itemListElement: [
          { "@type": "ListItem", position: 1, name: "Home", item: absoluteSiteUrl("/") },
          { "@type": "ListItem", position: 2, name: input.name, item: pageUrl },
        ],
      },
    ],
  };
}
