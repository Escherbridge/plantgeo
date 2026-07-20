import type { Session } from "next-auth";

export type TeamRole = "owner" | "member" | "viewer";

export interface AuthIdentity {
  userId?: string;
  teamId?: string;
  platformRole?: string;
}

export function identityFromSession(session: Session | null): AuthIdentity {
  const user = session?.user as
    | { id?: string; platformRole?: string }
    | undefined;
  return {
    userId: user?.id,
    platformRole: user?.platformRole,
  };
}

export function isPlatformAdmin(identity: AuthIdentity): boolean {
  return identity.platformRole === "admin";
}

export function isTeamEditorRole(role: string | null | undefined): boolean {
  return role === "owner" || role === "member";
}

export function canManageTeam(role: string | null | undefined): boolean {
  return role === "owner";
}

export function canInviteTeamRole(
  callerRole: string | null | undefined,
  requestedRole: TeamRole
): boolean {
  if (requestedRole === "owner") return false;
  if (callerRole === "owner") return true;
  return callerRole === "member" && requestedRole === "viewer";
}

export function wouldRemoveLastOwner(
  currentRole: string | null | undefined,
  nextRole: TeamRole | null,
  ownerCount: number
): boolean {
  return currentRole === "owner" && nextRole !== "owner" && ownerCount <= 1;
}
