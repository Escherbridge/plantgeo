import { z } from "zod";
import { TRPCError } from "@trpc/server";
import { and, eq, isNull } from "drizzle-orm";
import { type Context } from "@/lib/server/trpc/init";
import { teamMembers, teams, users } from "@/lib/server/db/schema";
import {
  DELEGABLE_TEAM_ROLES,
  resolveTeamRole,
  type TeamRole,
} from "@/lib/server/security/access-control";
import {
  normalizeEmail,
  type RedeemingIdentity,
  EMAIL_VERIFICATION_REQUIRED_MESSAGE,
} from "@/lib/server/security/invitations";

// Shared schemas, types and DB helpers for the teams (organizations) router.
// Rationale for the invariants enforced across every procedure group lives in
// `src/lib/server/AGENTS.md` §trpc / db / auth.

export const orgTypeSchema = z.enum([
  "nonprofit",
  "cooperative",
  "business",
  "individual",
  "government",
]);

/** Roles an invitation or join link may grant. `owner` is never delegable. */
export const delegableRoleSchema = z.enum(DELEGABLE_TEAM_ROLES);

/** Base64url secret shape shared by invitation tokens and join codes. */
export const secretSchema = z
  .string()
  .trim()
  .regex(/^[A-Za-z0-9_-]{16,512}$/, "Malformed token");

export type Database = Context["db"];

/** Everything both `db` and a transaction handle expose that this router uses. */
export type QueryRunner = Pick<
  Database,
  "select" | "insert" | "update" | "delete" | "execute"
>;

export interface Membership {
  isMember: boolean;
  role: TeamRole | null;
}

export interface MembershipResult {
  teamId: string;
  teamRole: TeamRole;
  orgName: string;
  orgSlug: string | null;
  alreadyMember: boolean;
}

export function sessionUserId(ctx: { session: Context["session"] }): string {
  return (ctx.session?.user as { id: string }).id;
}

export function sessionDisplayName(ctx: {
  session: Context["session"];
}): string {
  return ctx.session?.user?.name || ctx.session?.user?.email || "A teammate";
}

/**
 * Reads the address that authorizes a redemption from the database, never from
 * the session: the JWT email claim is minted at sign-in and never refreshed,
 * and `users.email_verified` exists nowhere in the token at all.
 */
export async function loadRedeemingIdentity(
  runner: QueryRunner,
  userId: string
): Promise<RedeemingIdentity> {
  const [row] = await runner
    .select({ email: users.email, emailVerified: users.emailVerified })
    .from(users)
    .where(eq(users.id, userId))
    .limit(1);
  return {
    email: row?.email ? normalizeEmail(row.email) : null,
    emailVerified: Boolean(row?.emailVerified),
  };
}

/**
 * An unverified address proves nothing about mailbox control, so it may not
 * claim anything addressed to that mailbox. Non-enumerating on purpose: the
 * message never says whether the invitation or link itself was valid.
 */
export function requireVerifiedIdentity(identity: RedeemingIdentity): string {
  if (!identity.email) {
    throw new TRPCError({
      code: "FORBIDDEN",
      message: "Your account needs an email address to join an organization",
    });
  }
  if (!identity.emailVerified) {
    throw new TRPCError({
      code: "FORBIDDEN",
      message: EMAIL_VERIFICATION_REQUIRED_MESSAGE,
    });
  }
  return identity.email;
}

/** Reads the caller's membership straight from the database, never the session. */
export async function loadMembership(
  runner: QueryRunner,
  teamId: string,
  userId: string
): Promise<Membership> {
  const [row] = await runner
    .select({ teamRole: teamMembers.teamRole })
    .from(teamMembers)
    .where(
      and(eq(teamMembers.teamId, teamId), eq(teamMembers.userId, userId))
    )
    .limit(1);
  if (!row) return { isMember: false, role: null };
  return { isMember: true, role: resolveTeamRole(row.teamRole) };
}

/**
 * Authorizes a caller against an organization. `allow` receives the role read
 * from the database; platform admins bypass membership entirely.
 */
export async function requireTeamAccess(
  runner: QueryRunner,
  teamId: string,
  userId: string,
  options: {
    platformAdmin?: boolean;
    allow?: (role: TeamRole | null) => boolean;
    message?: string;
  } = {}
): Promise<TeamRole | null> {
  const membership = await loadMembership(runner, teamId, userId);
  if (options.platformAdmin) {
    return membership.isMember ? membership.role : "owner";
  }
  const denied =
    !membership.isMember ||
    (options.allow ? !options.allow(membership.role) : false);
  if (denied) {
    throw new TRPCError({
      code: "FORBIDDEN",
      message: options.message ?? "Not authorized for this organization",
    });
  }
  return membership.role;
}

export async function loadOrganization(
  runner: QueryRunner,
  teamId: string
): Promise<{ id: string; name: string; slug: string | null }> {
  const [org] = await runner
    .select({ id: teams.id, name: teams.name, slug: teams.slug })
    .from(teams)
    .where(eq(teams.id, teamId))
    .limit(1);
  if (!org) {
    throw new TRPCError({ code: "NOT_FOUND", message: "Organization not found" });
  }
  return org;
}

/** Sets the caller's active organization only when they had none. */
export async function adoptActiveTeam(
  runner: QueryRunner,
  userId: string,
  teamId: string
): Promise<void> {
  await runner
    .update(users)
    .set({ activeTeamId: teamId })
    .where(and(eq(users.id, userId), isNull(users.activeTeamId)));
}
