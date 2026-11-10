import { absoluteSiteUrl } from "@/lib/public-site";

/** Head autodiscovery. Pages that set their own `alternates` must repeat this. */
export const RSS_ALTERNATE = { "application/rss+xml": "/feed.xml" } as const;
import { blogPath, ESSAYS } from "@/lib/writing/essays";

function xmlText(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

/** RFC 822 date for a real `YYYY-MM-DD` publication day, at 00:00 UTC. */
function rssDate(iso: string): string {
  const [year, month, day] = iso.split("-").map(Number);
  return new Date(Date.UTC(year, month - 1, day)).toUTCString();
}

/** Blog posts only. News and site-type pages are not items until one is published. */
export function blogRssXml(): string {
  const posts = [...ESSAYS].sort((a, b) => b.date.localeCompare(a.date) || a.slug.localeCompare(b.slug));
  const items = posts
    .map((essay) => {
      const url = absoluteSiteUrl(blogPath(essay));
      return [
        "    <item>",
        `      <title>${xmlText(essay.title)}</title>`,
        `      <link>${xmlText(url)}</link>`,
        `      <guid isPermaLink="true">${xmlText(url)}</guid>`,
        `      <pubDate>${rssDate(essay.date)}</pubDate>`,
        `      <description>${xmlText(essay.summary)}</description>`,
        "    </item>",
      ].join("\n");
    })
    .join("\n");
  const latest = posts[0]?.date;

  return [
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<rss version="2.0">',
    "  <channel>",
    "    <title>PlantGeo</title>",
    `    <link>${xmlText(absoluteSiteUrl("/blog"))}</link>`,
    "    <description>Notes from Escherbridge on PlantGeo.</description>",
    latest ? `    <lastBuildDate>${rssDate(latest)}</lastBuildDate>` : "",
    items,
    "  </channel>",
    "</rss>",
    "",
  ]
    .filter((line) => line !== "")
    .join("\n");
}
