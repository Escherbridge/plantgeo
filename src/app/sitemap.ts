import type { MetadataRoute } from "next";
import { SITEMAP_PATHS, absoluteSiteUrl } from "@/lib/public-site";

export default function sitemap(): MetadataRoute.Sitemap {
  return SITEMAP_PATHS.map((path) => ({
    url: absoluteSiteUrl(path),
  }));
}
