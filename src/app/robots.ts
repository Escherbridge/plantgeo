import type { MetadataRoute } from "next";
import { ROBOTS_DISALLOW, SITE_ORIGIN } from "@/lib/public-site";

export default function robots(): MetadataRoute.Robots {
  return {
    rules: {
      userAgent: "*",
      allow: "/",
      disallow: [...ROBOTS_DISALLOW],
    },
    sitemap: `${SITE_ORIGIN}/sitemap.xml`,
  };
}
