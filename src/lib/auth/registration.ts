import { z } from "zod";

export const MIN_PASSWORD_LENGTH = 8;
export const MAX_BCRYPT_PASSWORD_BYTES = 72;

export const REGISTRATION_ACKNOWLEDGEMENT =
  "Registration request received. Sign in with your account password or original sign-in method. If you already have an account, its password has not changed.";

/** Shared browser and server validation; blank optional names are omitted. */
export const registrationSchema = z.object({
  name: z.string().trim().max(100, "Name must be 100 characters or fewer.")
    .transform((value) => value || undefined).optional(),
  email: z.string().trim().toLowerCase().email("Enter a valid email address.")
    .max(254, "Email must be 254 characters or fewer."),
  password: z.string()
    .min(MIN_PASSWORD_LENGTH, `Password must be at least ${MIN_PASSWORD_LENGTH} characters.`)
    .max(MAX_BCRYPT_PASSWORD_BYTES, "Password is too long.")
    .refine((value) => new TextEncoder().encode(value).length <= MAX_BCRYPT_PASSWORD_BYTES,
      "Password is too long. Use fewer characters; accented characters and emoji take more space."),
}).strict();
