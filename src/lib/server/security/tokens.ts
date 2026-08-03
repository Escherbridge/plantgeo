import { createHash, randomBytes, timingSafeEqual } from "crypto";

/** Raw entropy per issued credential token; 32 bytes -> 43 base64url characters. */
const TOKEN_BYTES = 32;

/** Lifetimes, in milliseconds, for every single-use credential token we issue. */
export const TOKEN_TTL = {
  emailVerification: 24 * 60 * 60 * 1000,
  passwordReset: 60 * 60 * 1000,
  invitation: 7 * 24 * 60 * 60 * 1000,
} as const;

/** SHA-256 hex digest of a raw token; the only form that is ever persisted. */
export function hashToken(token: string): string {
  return createHash("sha256").update(token, "utf8").digest("hex");
}

/** Issue a single-use token: the raw value is emailed, the hash is stored. */
export function generateToken(): { token: string; tokenHash: string } {
  const token = randomBytes(TOKEN_BYTES).toString("base64url");
  return { token, tokenHash: hashToken(token) };
}

/** Absolute expiry for a token issued now with the given time-to-live. */
export function tokenExpiry(ttlMs: number): Date {
  return new Date(Date.now() + ttlMs);
}

/** Constant-time comparison for token hashes; length mismatch short-circuits. */
export function timingSafeTokenEqual(a: string, b: string): boolean {
  const left = Buffer.from(a, "utf8");
  const right = Buffer.from(b, "utf8");
  return left.length === right.length && timingSafeEqual(left, right);
}
