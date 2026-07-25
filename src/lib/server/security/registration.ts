import { createHash } from "crypto";
import { z } from "zod";

export const MAX_REGISTRATION_BODY_BYTES = 8_192;
export const MAX_BCRYPT_PASSWORD_BYTES = 72;
export const REGISTRATIONS_PER_MINUTE = 5;

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

export function registrationRateLimitKey(request: Request): string {
  const forwarded = request.headers.get("x-forwarded-for")?.split(",")[0];
  const address =
    request.headers.get("cf-connecting-ip")?.trim() ||
    forwarded?.trim() ||
    request.headers.get("x-real-ip")?.trim() ||
    "unknown";
  const digest = createHash("sha256").update(address).digest("hex");
  return `auth:register:${digest}`;
}

export function isUniqueConstraintViolation(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    "code" in error &&
    error.code === "23505"
  );
}
