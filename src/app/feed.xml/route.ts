import { blogRssXml } from "@/lib/content/rss";

export function GET() {
  return new Response(blogRssXml(), {
    headers: {
      "Content-Type": "application/rss+xml; charset=utf-8",
    },
  });
}
