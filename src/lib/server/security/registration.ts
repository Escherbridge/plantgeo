import { createHash } from "crypto";
import { z } from "zod";

export const MAX_REGISTRATION_BODY_BYTES = 8_192;
export const MAX_BCRYPT_PASSWORD_BYTES = 72;
export const REGISTRATIONS_PER_MINUTE = 5;

export const MAX_VERIFY_EMAIL_BODY_BYTES = 2_048;
export const MAX_FORGOT_PASSWORD_BODY_BYTES = 2_048;
export const MAX_RESET_PASSWORD_BODY_BYTES = 4_096;

export const VERIFY_EMAIL_PER_MINUTE = 10;
export const FORGOT_PASSWORD_PER_MINUTE = 3;
export const RESET_PASSWORD_PER_MINUTE = 5;

/**
 * Credential sign-in needs both dimensions: the per-IP budget stops one host
 * from spraying many accounts, and the per-account budget stops a botnet from
 * spraying one account from many hosts. Either alone is trivially defeated.
 */
export const CREDENTIAL_SIGNIN_PER_IP_PER_MINUTE = 10;
export const CREDENTIAL_SIGNIN_PER_ACCOUNT_PER_MINUTE = 5;

/**
 * Fixed response floor for forgot-password. Every outcome — unknown address,
 * OAuth-only account, real reset issued — must land in the same timing class,
 * and this floor has to exceed the slowest work left on the request path.
 */
export const FORGOT_PASSWORD_MIN_DURATION_MS = 500;

/** Base64url alphabet only; rejects padding, whitespace and path separators. */
const TOKEN_PATTERN = /^[A-Za-z0-9_-]{16,512}$/;

export const registrationSchema = z
  .object({
    name: z.string().trim().min(1).max(100).optional(),
    email: z.string().trim().toLowerCase().email().max(254),
    password: z
      .string()
      .min(8)
      .max(MAX_BCRYPT_PASSWORD_BYTES)
      .refine(
        (password) =>
          Buffer.byteLength(password, "utf8") <= MAX_BCRYPT_PASSWORD_BYTES,
        "Password is too long"
      ),
  })
  .strict();

export const emailVerificationSchema = z
  .object({ token: z.string().trim().regex(TOKEN_PATTERN) })
  .strict();

export const forgotPasswordSchema = z
  .object({ email: z.string().trim().toLowerCase().email().max(254) })
  .strict();

export const passwordResetSchema = z
  .object({
    token: z.string().trim().regex(TOKEN_PATTERN),
    password: registrationSchema.shape.password,
  })
  .strict();

/** Rate-limit identity for anonymous requests: the caller IP, never stored raw. */
export function ipRateLimitKey(request: Request, prefix: string): string {
  const forwarded = request.headers.get("x-forwarded-for")?.split(",")[0];
  const address =
    request.headers.get("cf-connecting-ip")?.trim() ||
    forwarded?.trim() ||
    request.headers.get("x-real-ip")?.trim() ||
    "unknown";
  const digest = createHash("sha256").update(address).digest("hex");
  return `${prefix}:${digest}`;
}

export function registrationRateLimitKey(request: Request): string {
  return ipRateLimitKey(request, "auth:register");
}

export function emailVerificationRateLimitKey(request: Request): string {
  return ipRateLimitKey(request, "auth:verify-email");
}

export function forgotPasswordRateLimitKey(request: Request): string {
  return ipRateLimitKey(request, "auth:forgot-password");
}

export function passwordResetRateLimitKey(request: Request): string {
  return ipRateLimitKey(request, "auth:reset-password");
}

export function credentialSignInIpRateLimitKey(request: Request): string {
  return ipRateLimitKey(request, "auth:signin");
}

/**
 * Per-account budget keyed on the digest of the submitted address, so the
 * limiter never stores raw emails and an unknown address costs the same as a
 * real one.
 */
export function credentialSignInAccountRateLimitKey(email: string): string {
  const digest = createHash("sha256")
    .update(normalizeAuthEmail(email))
    .digest("hex");
  return `auth:signin:account:${digest}`;
}

/**
 * Registration lowercases before storing, so every later lookup must apply the
 * same normalization or `Alice@Ex.com` silently fails to match `alice@ex.com`.
 */
export function normalizeAuthEmail(email: string): string {
  return email.trim().toLowerCase();
}

/** Holds a response until `floorMs` has elapsed, collapsing timing side channels. */
export async function padToMinimumDuration(
  startedAt: number,
  floorMs: number
): Promise<void> {
  const remaining = floorMs - (Date.now() - startedAt);
  if (remaining <= 0) return;
  await new Promise((resolve) => setTimeout(resolve, remaining));
}

export function isUniqueConstraintViolation(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    "code" in error &&
    error.code === "23505"
  );
}
