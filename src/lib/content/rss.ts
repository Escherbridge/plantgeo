import { SITE_TYPES, siteTypePath } from "@/lib/content/site-types";
import { absoluteSiteUrl } from "@/lib/public-site";
import { blogPath, ESSAYS } from "@/lib/writing/essays";

/** Head autodiscovery. Pages that set their own `alternates` must repeat this. */
export const RSS_ALTERNATE = { "application/rss+xml": "/feed.xml" } as const;

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

interface FeedEntry {
  title: string;
  path: string;
  date: string;
  summary: string;
}

/** Blog posts and published site-type pages. News stays out until a post exists. */
export function blogRssXml(): string {
  const posts: FeedEntry[] = [
    ...ESSAYS.map((essay) => ({
      title: essay.title,
      path: blogPath(essay),
      date: essay.date,
      summary: essay.summary,
    })),
    ...SITE_TYPES.map((page) => ({
      title: page.h1,
      path: siteTypePath(page),
      date: page.publishedAt,
      summary: page.metaDescription,
    })),
  ].sort((a, b) => b.date.localeCompare(a.date) || a.path.localeCompare(b.path));
  const items = posts
    .map((entry) => {
      const url = absoluteSiteUrl(entry.path);
      return [
        "    <item>",
        `      <title>${xmlText(entry.title)}</title>`,
        `      <link>${xmlText(url)}</link>`,
        `      <guid isPermaLink="true">${xmlText(url)}</guid>`,
        `      <pubDate>${rssDate(entry.date)}</pubDate>`,
        `      <description>${xmlText(entry.summary)}</description>`,
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
