/** Canonical origin for public URLs. Hardcoded; do not read this from the environment. */
export const SITE_ORIGIN = "https://plantgeo.aevani.com";

/** Paths Q1 requires in /sitemap.xml. Essay routes are appended by the writing index. */
export const SITEMAP_PATHS = [
  "/",
  "/contact",
  "/privacy",
  "/terms",
  "/affiliate",
  "/plan/new",
  "/plan/sample-ada-county",
] as const;

export function absoluteSiteUrl(path: string): string {
  if (path === "/") return `${SITE_ORIGIN}/`;
  return `${SITE_ORIGIN}${path}`;
}
