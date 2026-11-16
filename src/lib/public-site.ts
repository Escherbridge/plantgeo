/** Canonical origin for public URLs. Hardcoded; do not read this from the environment. */
export const SITE_ORIGIN = "https://plantgeo.aevani.com";

/** Account, admin, and API surfaces stay out of the index. */
export const PRIVATE_ROUTE_ROBOTS = { index: false, follow: false } as const;

export const ROBOTS_DISALLOW = [
  "/api/",
  "/admin",
  "/dashboard",
  "/embed",
  "/forgot-password",
  "/invite/",
  "/join/",
  "/login",
  "/moderation",
  "/onboarding",
  "/register",
  "/reset-password",
  "/verify-email",
] as const;

/** Paths Q1 requires in /sitemap.xml. Essay routes are added beside this list. */
export const SITEMAP_PATHS = [
  "/",
  "/about",
  "/community",
  "/contact",
  "/docs",
  "/feed",
  "/privacy",
  "/terms",
  "/affiliate",
  "/plan/new",
  "/plan/sample",
  "/plan/sample-ada-county",
] as const;

export function absoluteSiteUrl(path: string): string {
  if (path === "/") return `${SITE_ORIGIN}/`;
  return `${SITE_ORIGIN}${path}`;
}
