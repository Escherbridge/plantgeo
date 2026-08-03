import { randomBytes } from "crypto";
import { eq } from "drizzle-orm";
import type { db } from "@/lib/server/db";
import { teams } from "@/lib/server/db/schema";

/** Maximum length of an organization slug (matches teams.slug varchar(100)). */
export const MAX_SLUG_LENGTH = 100;

/** Slugs the router reserves for platform routes and future namespaces. */
export const RESERVED_SLUGS: ReadonlySet<string> = new Set([
  "api",
  "admin",
  "dashboard",
  "login",
  "register",
  "onboarding",
  "invite",
  "join",
  "docs",
  "embed",
  "settings",
  "new",
  "about",
  "support",
  "status",
  "www",
]);

/** Any drizzle handle (database or transaction) able to read the teams table. */
export type SlugQueryRunner = Pick<typeof db, "select">;

/** Thrown when bounded retries cannot produce a free slug. */
export class SlugGenerationError extends Error {
  constructor(base: string) {
    super(`Could not derive an available organization slug from "${base}"`);
    this.name = "SlugGenerationError";
  }
}

/** Lowercases and strips a name down to `[a-z0-9-]` with no repeated hyphens. */
export function slugify(input: string): string {
  return input
    .normalize("NFKD")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, MAX_SLUG_LENGTH)
    .replace(/-$/, "");
}

/** True when the slug collides with a platform route or reserved namespace. */
export function isReservedSlug(slug: string): boolean {
  return RESERVED_SLUGS.has(slug.trim().toLowerCase());
}

/** True when the slug is already normalized and within the allowed shape. */
export function isValidSlug(slug: string): boolean {
  if (slug.length === 0 || slug.length > MAX_SLUG_LENGTH) return false;
  return /^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(slug);
}

/**
 * Normalizes a user-supplied slug, rejecting anything that cannot be used.
 * Returns null when the input normalizes to an empty or reserved slug.
 */
export function normalizeSlug(slug: string): string | null {
  const normalized = slugify(slug);
  if (!isValidSlug(normalized)) return null;
  if (isReservedSlug(normalized)) return null;
  return normalized;
}

function randomSlugSuffix(): string {
  return randomBytes(3).toString("hex");
}

function withSuffix(root: string, suffix: string): string {
  const budget = MAX_SLUG_LENGTH - suffix.length - 1;
  const trimmed = root.slice(0, Math.max(1, budget)).replace(/-+$/, "");
  return `${trimmed || "org"}-${suffix}`;
}

async function slugExists(
  runner: SlugQueryRunner,
  slug: string
): Promise<boolean> {
  const rows = await runner
    .select({ id: teams.id })
    .from(teams)
    .where(eq(teams.slug, slug))
    .limit(1);
  return rows.length > 0;
}

/**
 * Derives a free slug from `base`, appending a short random suffix on collision.
 * Retries are bounded; the caller still has to handle a unique-violation race.
 */
export async function generateUniqueSlug(
  runner: SlugQueryRunner,
  base: string,
  maxAttempts = 8
): Promise<string> {
  const root = slugify(base) || "org";
  const candidates: string[] = [];
  if (isValidSlug(root) && !isReservedSlug(root)) candidates.push(root);

  for (let attempt = candidates.length; attempt < maxAttempts; attempt += 1) {
    candidates.push(withSuffix(root, randomSlugSuffix()));
  }

  for (const candidate of candidates) {
    if (!(await slugExists(runner, candidate))) return candidate;
  }
  throw new SlugGenerationError(base);
}
