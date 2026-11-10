import type { MetadataRoute } from "next";
import { SITEMAP_PATHS, absoluteSiteUrl } from "@/lib/public-site";
import { WRITING_SITEMAP_PATHS } from "@/lib/writing/essays";

const SITEMAP_ENTRIES = [...SITEMAP_PATHS, ...WRITING_SITEMAP_PATHS];

export default function sitemap(): MetadataRoute.Sitemap {
  return SITEMAP_ENTRIES.map((path) => ({
    url: absoluteSiteUrl(path),
  }));
}
