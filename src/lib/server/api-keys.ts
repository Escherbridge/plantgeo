import { createHash, randomBytes } from "crypto";
import { z } from "zod";

export const API_KEY_PERMISSIONS = [
  "read:context",
  "read:features",
  "read:geocode",
  "read:layers",
  "read:routes",
  "read:teams",
] as const;

export type ApiKeyPermission = (typeof API_KEY_PERMISSIONS)[number];

export const DEFAULT_API_KEY_PERMISSIONS: ApiKeyPermission[] = [
  ...API_KEY_PERMISSIONS,
];

export const DEFAULT_API_KEY_RATE_LIMIT = 1_000;
export const MAX_API_KEY_RATE_LIMIT = 10_000;
export const API_KEY_LAST_USED_REFRESH_MS = 15 * 60 * 1_000;

const API_KEY_PATTERN = /^pg_[a-f0-9]{64}$/i;
const LEGACY_API_KEY_PATTERN = /^[a-f0-9]{64}$/i;
const permissionSchema = z.enum(API_KEY_PERMISSIONS);
const optionalTeamIdSchema = z.string().uuid().nullable().optional();

const baseApiKeyIssuanceSchema = z.object({
  permissions: z
    .array(permissionSchema)
    .min(1)
    .max(API_KEY_PERMISSIONS.length)
    .optional()
    .default(DEFAULT_API_KEY_PERMISSIONS),
  rateLimit: z
    .number()
    .int()
    .min(1)
    .max(MAX_API_KEY_RATE_LIMIT)
    .optional()
    .default(DEFAULT_API_KEY_RATE_LIMIT),
  teamId: optionalTeamIdSchema,
});

export const personalApiKeyIssuanceSchema = baseApiKeyIssuanceSchema
  .extend({
    name: z.string().trim().min(1).max(100).optional().default("Personal API key"),
  })
  .strict();

export const adminApiKeyIssuanceSchema = baseApiKeyIssuanceSchema
  .extend({
    name: z.string().trim().min(1).max(100),
    userId: z.string().uuid().nullable().optional(),
  })
  .strict();

/** Generates a displayable API key; only its SHA-256 digest is persisted. */
export function generateApiKey(): string {
  return `pg_${randomBytes(32).toString("hex")}`;
}

/** Produces the fixed-length, indexable API-key digest used for lookup. */
export function hashApiKey(rawKey: string): string {
  return createHash("sha256").update(rawKey).digest("hex");
}

/** Rejects malformed values before hashing or querying the database. */
export function isSupportedApiKey(rawKey: string): boolean {
  return API_KEY_PATTERN.test(rawKey) || LEGACY_API_KEY_PATTERN.test(rawKey);
}

export function isApiKeyPermission(value: string): value is ApiKeyPermission {
  return (API_KEY_PERMISSIONS as readonly string[]).includes(value);
}

export function hasRequiredApiKeyPermission(
  permissions: ApiKeyPermission[] | undefined,
  requiredPermission: ApiKeyPermission
): boolean {
  return permissions?.includes(requiredPermission) ?? false;
}

export function shouldRefreshApiKeyLastUsed(
  lastUsed: Date | null,
  now = new Date()
): boolean {
  return (
    !lastUsed || now.getTime() - lastUsed.getTime() >= API_KEY_LAST_USED_REFRESH_MS
  );
}
