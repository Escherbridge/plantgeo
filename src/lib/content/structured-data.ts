import { absoluteSiteUrl, SITE_ORIGIN } from "@/lib/public-site";
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
