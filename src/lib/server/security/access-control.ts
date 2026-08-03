import type { Session } from "next-auth";

export const TEAM_ROLES = ["owner", "member", "viewer"] as const;

export type TeamRole = (typeof TEAM_ROLES)[number];

/** Roles an invitation or join link may ever grant; `owner` is never delegable. */
export const DELEGABLE_TEAM_ROLES = ["member", "viewer"] as const;

export type DelegableTeamRole = (typeof DELEGABLE_TEAM_ROLES)[number];

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

/** Narrows a stored/claimed role string to a known team role, else null. */
export function resolveTeamRole(role: unknown): TeamRole | null {
  return typeof role === "string" && (TEAM_ROLES as readonly string[]).includes(role)
    ? (role as TeamRole)
    : null;
}

/** Type guard form of {@link resolveTeamRole}. */
export function isTeamRole(role: unknown): role is TeamRole {
  return resolveTeamRole(role) !== null;
}

/**
 * Clamp for every role a redemption may grant: anything outside
 * {@link DELEGABLE_TEAM_ROLES} — including a stored `owner` from a legacy row,
 * a seed or a future admin path — degrades to `viewer` instead of escalating.
 */
export function resolveDelegableRole(role: unknown): DelegableTeamRole {
  return typeof role === "string" &&
    (DELEGABLE_TEAM_ROLES as readonly string[]).includes(role)
    ? (role as DelegableTeamRole)
    : "viewer";
}

/** Owners and members may invite; viewers may not. */
export function canManageInvitations(role: string | null | undefined): boolean {
  return role === "owner" || role === "member";
}

/** Shareable join links are an owner-only credential. */
export function canCreateJoinLink(role: string | null | undefined): boolean {
  return role === "owner";
}

/** Revoking or rotating a join link is owner-only. */
export function canRevokeJoinLink(role: string | null | undefined): boolean {
  return role === "owner";
}

export function wouldRemoveLastOwner(
  currentRole: string | null | undefined,
  nextRole: TeamRole | null,
  ownerCount: number
): boolean {
  return currentRole === "owner" && nextRole !== "owner" && ownerCount <= 1;
}
