import { generateToken, hashToken, timingSafeTokenEqual } from "@/lib/server/security/tokens";

/** Issues the raw guest token once. Only `tokenHash` is persisted. */
export function issueGuestPlanToken(): { token: string; tokenHash: string } {
  return generateToken();
}

/** SHA-256 hex digest. The same function invitation tokens use. */
export function hashGuestPlanToken(token: string): string {
  return hashToken(token);
}

export function guestTokenMatches(
  presented: string | null | undefined,
  storedHash: string | null | undefined
): boolean {
  if (!presented || !storedHash) return false;
  return timingSafeTokenEqual(hashGuestPlanToken(presented), storedHash);
}

/**
 * A sample plan is never editable. A signed-in owner or a matching guest token
 * can edit; either one is enough, and a missing credential is not.
 */
export function canEditSitePlan(input: {
  isSample: boolean;
  ownerUserId: string | null;
  sessionUserId: string | null;
  guestToken: string | null | undefined;
  guestTokenHash: string | null;
}): boolean {
  if (input.isSample) return false;
  if (
    input.ownerUserId !== null &&
    input.sessionUserId !== null &&
    input.ownerUserId === input.sessionUserId
  ) {
    return true;
  }
  return guestTokenMatches(input.guestToken, input.guestTokenHash);
}
