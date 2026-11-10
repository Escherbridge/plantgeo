/** First-party cookie that keeps an invite code for a later "start your plan". */
export const PLAN_REF_COOKIE = "plantgeo_ref";

/** Short-lived on purpose: two weeks, then a new visit must carry the code again. */
export const PLAN_REF_MAX_AGE_SECONDS = 14 * 24 * 60 * 60;

const REF_CODE_PATTERN = /^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$/;

/** Accepts a short invite code. Anything else is ignored, not stored. */
export function normalizeRefCode(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const normalized = value.trim().toLowerCase();
  if (!REF_CODE_PATTERN.test(normalized)) return null;
  return normalized;
}

/** Reads `plantgeo_ref` from a Cookie header. The raw address is never part of the value. */
export function readPlanRefCookie(cookieHeader: string | null | undefined): string | null {
  if (!cookieHeader) return null;
  const prefix = `${PLAN_REF_COOKIE}=`;
  for (const part of cookieHeader.split(";")) {
    const trimmed = part.trim();
    if (!trimmed.startsWith(prefix)) continue;
    try {
      return normalizeRefCode(decodeURIComponent(trimmed.slice(prefix.length)));
    } catch {
      return null;
    }
  }
  return null;
}
