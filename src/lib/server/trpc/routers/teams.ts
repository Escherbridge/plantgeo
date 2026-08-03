import { z } from "zod";
import { TRPCError } from "@trpc/server";
import { and, eq, isNull, or, sql, type SQL } from "drizzle-orm";
import {
  router,
  publicProcedure,
  protectedProcedure,
  contributorProcedure,
  adminProcedure,
  type Context,
} from "@/lib/server/trpc/init";
import {
  teams,
  teamMembers,
  teamInvitations,
  teamJoinLinks,
  users,
} from "@/lib/server/db/schema";
import {
  canCreateJoinLink,
  canInviteTeamRole,
  canManageInvitations,
  canRevokeJoinLink,
  DELEGABLE_TEAM_ROLES,
  identityFromSession,
  isPlatformAdmin,
  isTeamEditorRole,
  resolveDelegableRole,
  resolveTeamRole,
  wouldRemoveLastOwner,
  type TeamRole,
} from "@/lib/server/security/access-control";
import {
  canRedeemJoinLink,
  emailMatchesInvitation,
  EMAIL_VERIFICATION_REQUIRED_MESSAGE,
  INVITATIONS_PER_MINUTE,
  invitationRateLimitKey,
  invitationRejectionMessage,
  invitationState,
  joinLinkRejectionMessage,
  joinLinkState,
  normalizeEmail,
  normalizeEmailDomain,
  type RedeemingIdentity,
} from "@/lib/server/security/invitations";
import {
  generateUniqueSlug,
  normalizeSlug,
  SlugGenerationError,
} from "@/lib/server/security/org-slug";
import { isUniqueConstraintViolation } from "@/lib/server/security/registration";
import { checkRateLimit } from "@/lib/server/middleware/api-auth";
import {
  generateToken,
  hashToken,
  timingSafeTokenEqual,
  tokenExpiry,
  TOKEN_TTL,
} from "@/lib/server/security/tokens";
import {
  appUrl,
  sendOrgInvitation,
} from "@/lib/server/services/transactional-email";

// "Organizations" are the teams/team_members tables. Invariants enforced here:
// membership, role and the caller's email address are always re-read from the
// database inside the acting transaction (never trusted from the JWT, which is
// minted at sign-in and never refreshed), only token/code hashes are stored,
// `owner` is never delegable through an invitation or join link, and every
// redemption is single-use and idempotent. The state machine itself lives in
// `@/lib/server/security/invitations`.

const orgTypeSchema = z.enum([
  "nonprofit",
  "cooperative",
  "business",
  "individual",
  "government",
]);

/** Roles an invitation or join link may grant. `owner` is never delegable. */
const delegableRoleSchema = z.enum(DELEGABLE_TEAM_ROLES);

/** Base64url secret shape shared by invitation tokens and join codes. */
const secretSchema = z
  .string()
  .trim()
  .regex(/^[A-Za-z0-9_-]{16,512}$/, "Malformed token");

const MAX_JOIN_LINK_TTL_MS = 365 * 24 * 60 * 60 * 1000;

const PARTNER_DIRECTORY_UNAVAILABLE_MESSAGE =
  "Partner discovery is unavailable until verified organizations and access rules are published";

const OPPORTUNITY_WAYPOINTS_UNAVAILABLE_MESSAGE =
  "Opportunity waypoints are inactive until a reviewed, workspace-scoped warehouse publication is available";

type Database = Context["db"];

/** Everything both `db` and a transaction handle expose that this router uses. */
type QueryRunner = Pick<
  Database,
  "select" | "insert" | "update" | "delete" | "execute"
>;

interface Membership {
  isMember: boolean;
  role: TeamRole | null;
}

function sessionUserId(ctx: { session: Context["session"] }): string {
  return (ctx.session?.user as { id: string }).id;
}

function sessionDisplayName(ctx: { session: Context["session"] }): string {
  return ctx.session?.user?.name || ctx.session?.user?.email || "A teammate";
}

/**
 * Reads the address that authorizes a redemption from the database, never from
 * the session: the JWT email claim is minted at sign-in and never refreshed,
 * and `users.email_verified` exists nowhere in the token at all.
 */
async function loadRedeemingIdentity(
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
function requireVerifiedIdentity(identity: RedeemingIdentity): string {
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
async function loadMembership(
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
async function requireTeamAccess(
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

async function loadOrganization(
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
async function adoptActiveTeam(
  runner: QueryRunner,
  userId: string,
  teamId: string
): Promise<void> {
  await runner
    .update(users)
    .set({ activeTeamId: teamId })
    .where(and(eq(users.id, userId), isNull(users.activeTeamId)));
}

/**
 * Guard for the atomic join-link claim: the same predicate is evaluated inside
 * the incrementing UPDATE so concurrent redeems can never exceed `maxUses`.
 */
export function joinLinkClaimCondition(linkId: string, now: Date): SQL {
  return and(
    eq(teamJoinLinks.id, linkId),
    isNull(teamJoinLinks.revokedAt),
    or(isNull(teamJoinLinks.expiresAt), sql`${teamJoinLinks.expiresAt} > ${now}`),
    or(
      isNull(teamJoinLinks.maxUses),
      sql`${teamJoinLinks.useCount} < ${teamJoinLinks.maxUses}`
    )
  ) as SQL;
}

function invitationAcceptUrl(token: string): string {
  return `${appUrl()}/api/invitations/${encodeURIComponent(token)}`;
}

function joinLinkUrl(code: string): string {
  return `${appUrl()}/api/join/${encodeURIComponent(code)}`;
}

interface IssuedInvitation {
  id: string;
  email: string;
  teamRole: TeamRole;
  expiresAt: Date;
  /**
   * Live credential. Returned only when the caller passed `returnLink`, empty
   * otherwise; the invitee always receives it by email regardless.
   */
  acceptUrl: string;
}

/**
 * Consent-based invitation issuance shared by `createInvitation` and the
 * deprecated `inviteMember` shim. Only the token hash is ever persisted.
 */
async function issueInvitation(
  ctx: { db: Database; session: Context["session"] },
  params: {
    teamId: string;
    email: string;
    teamRole: "member" | "viewer";
    returnLink: boolean;
  }
): Promise<IssuedInvitation> {
  const callerId = sessionUserId(ctx);
  const platformAdmin = isPlatformAdmin(identityFromSession(ctx.session));
  const email = normalizeEmail(params.email);

  // Authorization precedes the rate-limit check so an unauthorized caller
  // cannot burn the quota of the organization they are probing.
  const callerRole = await loadMembership(ctx.db, params.teamId, callerId);
  if (
    !platformAdmin &&
    !canInviteTeamRole(callerRole.role, params.teamRole)
  ) {
    throw new TRPCError({
      code: "FORBIDDEN",
      message: "Not authorized to invite this organization role",
    });
  }

  const limit = await checkRateLimit(
    invitationRateLimitKey(callerId),
    INVITATIONS_PER_MINUTE
  );
  if (limit.available && limit.limited) {
    throw new TRPCError({
      code: "TOO_MANY_REQUESTS",
      message: "Too many invitations sent, try again shortly",
    });
  }

  const org = await loadOrganization(ctx.db, params.teamId);

  const [alreadyMember] = await ctx.db
    .select({ userId: users.id })
    .from(users)
    .innerJoin(
      teamMembers,
      and(
        eq(teamMembers.userId, users.id),
        eq(teamMembers.teamId, params.teamId)
      )
    )
    .where(eq(sql`lower(${users.email})`, email))
    .limit(1);
  if (alreadyMember) {
    throw new TRPCError({
      code: "CONFLICT",
      message: "That person is already a member of this organization",
    });
  }

  const { token, tokenHash } = generateToken();
  const expiresAt = tokenExpiry(TOKEN_TTL.invitation);

  const invitation = await ctx.db.transaction(async (transaction) => {
    const [pending] = await transaction
      .select({ id: teamInvitations.id, teamRole: teamInvitations.teamRole })
      .from(teamInvitations)
      .where(
        and(
          eq(teamInvitations.teamId, params.teamId),
          eq(teamInvitations.email, email),
          isNull(teamInvitations.acceptedAt),
          isNull(teamInvitations.revokedAt)
        )
      )
      .limit(1)
      .for("update");

    if (pending) {
      // Replacing a pending invitation invalidates its emailed link, so the
      // caller must be allowed to have issued the pending role in the first
      // place: otherwise a member could silently downgrade and kill an owner's
      // outstanding invite. Re-sending the same or a lower role still works.
      const pendingRole = resolveTeamRole(pending.teamRole);
      if (
        !platformAdmin &&
        (pendingRole === null || !canInviteTeamRole(callerRole.role, pendingRole))
      ) {
        throw new TRPCError({
          code: "CONFLICT",
          message:
            "That address already has a pending invitation you are not allowed to replace",
        });
      }

      const [replaced] = await transaction
        .update(teamInvitations)
        .set({
          teamRole: params.teamRole,
          tokenHash,
          invitedBy: callerId,
          expiresAt,
          createdAt: new Date(),
        })
        .where(eq(teamInvitations.id, pending.id))
        .returning({
          id: teamInvitations.id,
          email: teamInvitations.email,
          teamRole: teamInvitations.teamRole,
          expiresAt: teamInvitations.expiresAt,
        });
      return replaced;
    }

    const [created] = await transaction
      .insert(teamInvitations)
      .values({
        teamId: params.teamId,
        email,
        teamRole: params.teamRole,
        tokenHash,
        invitedBy: callerId,
        expiresAt,
      })
      .returning({
        id: teamInvitations.id,
        email: teamInvitations.email,
        teamRole: teamInvitations.teamRole,
        expiresAt: teamInvitations.expiresAt,
      });
    return created;
  });

  const acceptUrl = invitationAcceptUrl(token);
  try {
    await sendOrgInvitation(email, {
      orgName: org.name,
      inviterName: sessionDisplayName(ctx),
      acceptUrl,
      role: params.teamRole,
    });
  } catch {
    // Delivery failures must not roll back the invitation: when the caller
    // asked for the link they can still share it out of band.
  }

  return {
    id: invitation.id,
    email: invitation.email,
    teamRole: resolveTeamRole(invitation.teamRole) ?? params.teamRole,
    expiresAt: invitation.expiresAt,
    // Opt-in: a live invitation token in a tRPC response body ends up in any
    // response logging or client error reporter that sees it.
    acceptUrl: params.returnLink ? acceptUrl : "",
  };
}

/**
 * Public previews are discriminated unions: an invalid token resolves to
 * `{ valid: false }` and nothing else, so nothing about the organization can be
 * enumerated by guessing tokens.
 */
type InvitationPreview =
  | { valid: true; orgName: string; email: string; role: TeamRole }
  | { valid: false; orgName?: undefined; email?: undefined; role?: undefined };

type JoinLinkPreview =
  | { valid: true; orgName: string; role: TeamRole }
  | { valid: false; orgName?: undefined; role?: undefined };

interface MembershipResult {
  teamId: string;
  teamRole: TeamRole;
  orgName: string;
  orgSlug: string | null;
  alreadyMember: boolean;
}

export const teamsRouter = router({
  // ─── Organization basics ───────────────────────────────────────────────

  listMyTeams: protectedProcedure.query(async ({ ctx }) => {
    const userId = sessionUserId(ctx);
    const rows = await ctx.db
      .select({ team: teams, role: teamMembers.teamRole })
      .from(teamMembers)
      .innerJoin(teams, eq(teamMembers.teamId, teams.id))
      .where(eq(teamMembers.userId, userId));
    return rows;
  }),

  createTeam: contributorProcedure
    .input(
      z.object({
        name: z.string().trim().min(1).max(100),
        slug: z.string().trim().min(1).max(100).optional(),
        description: z.string().max(2000).optional(),
        orgType: orgTypeSchema.optional(),
        specialties: z.array(z.string().max(100)).max(50).optional(),
        website: z.string().url().optional(),
        serviceArea: z.record(z.unknown()).optional(),
      })
    )
    .mutation(async ({ ctx, input }) => {
      const userId = sessionUserId(ctx);
      const requestedSlug = input.slug ? normalizeSlug(input.slug) : null;
      if (input.slug && !requestedSlug) {
        throw new TRPCError({
          code: "BAD_REQUEST",
          message: "That organization URL is not available",
        });
      }

      try {
        return await ctx.db.transaction(async (transaction) => {
          if (requestedSlug) {
            const [taken] = await transaction
              .select({ id: teams.id })
              .from(teams)
              .where(eq(teams.slug, requestedSlug))
              .limit(1);
            if (taken) {
              throw new TRPCError({
                code: "CONFLICT",
                message: "That organization URL is already taken",
              });
            }
          }
          const slug =
            requestedSlug ?? (await generateUniqueSlug(transaction, input.name));

          const [team] = await transaction
            .insert(teams)
            .values({
              name: input.name,
              slug,
              description: input.description,
              orgType: input.orgType,
              specialties: input.specialties ?? [],
              website: input.website,
              serviceArea: input.serviceArea ?? null,
              createdBy: userId,
            })
            .returning();

          await transaction.insert(teamMembers).values({
            teamId: team.id,
            userId,
            teamRole: "owner",
          });
          await adoptActiveTeam(transaction, userId, team.id);
          return team;
        });
      } catch (error) {
        if (error instanceof TRPCError) throw error;
        if (error instanceof SlugGenerationError) {
          throw new TRPCError({
            code: "CONFLICT",
            message: "Could not derive a unique organization URL",
          });
        }
        if (isUniqueConstraintViolation(error)) {
          throw new TRPCError({
            code: "CONFLICT",
            message: "That organization URL is already taken",
          });
        }
        throw error;
      }
    }),

  updateTeam: contributorProcedure
    .input(
      z.object({
        id: z.string().uuid(),
        name: z.string().trim().min(1).max(100).optional(),
        slug: z.string().trim().min(1).max(100).optional(),
        description: z.string().max(2000).optional(),
        orgType: orgTypeSchema.optional(),
        specialties: z.array(z.string().max(100)).max(50).optional(),
        website: z.string().url().optional().nullable(),
        serviceArea: z.record(z.unknown()).optional().nullable(),
      })
    )
    .mutation(async ({ ctx, input }) => {
      const userId = sessionUserId(ctx);
      const platformAdmin = isPlatformAdmin(identityFromSession(ctx.session));
      const role = await requireTeamAccess(ctx.db, input.id, userId, {
        platformAdmin,
        allow: isTeamEditorRole,
        message: "Not authorized to update this organization",
      });

      const updates: Partial<typeof teams.$inferInsert> = {};
      if (input.name !== undefined) updates.name = input.name;
      if (input.description !== undefined) updates.description = input.description;
      if (input.orgType !== undefined) updates.orgType = input.orgType;
      if (input.specialties !== undefined) updates.specialties = input.specialties;
      if (input.website !== undefined) updates.website = input.website;
      if (input.serviceArea !== undefined) {
        updates.serviceArea = input.serviceArea ?? null;
      }

      if (input.slug !== undefined) {
        if (!platformAdmin && role !== "owner") {
          throw new TRPCError({
            code: "FORBIDDEN",
            message: "Only an owner can change the organization URL",
          });
        }
        const nextSlug = normalizeSlug(input.slug);
        if (!nextSlug) {
          throw new TRPCError({
            code: "BAD_REQUEST",
            message: "That organization URL is not available",
          });
        }
        const [taken] = await ctx.db
          .select({ id: teams.id })
          .from(teams)
          .where(eq(teams.slug, nextSlug))
          .limit(1);
        if (taken && taken.id !== input.id) {
          throw new TRPCError({
            code: "CONFLICT",
            message: "That organization URL is already taken",
          });
        }
        updates.slug = nextSlug;
      }

      if (Object.keys(updates).length === 0) {
        const [current] = await ctx.db
          .select()
          .from(teams)
          .where(eq(teams.id, input.id))
          .limit(1);
        if (!current) {
          throw new TRPCError({
            code: "NOT_FOUND",
            message: "Organization not found",
          });
        }
        return current;
      }

      try {
        const [updated] = await ctx.db
          .update(teams)
          .set(updates)
          .where(eq(teams.id, input.id))
          .returning();
        if (!updated) {
          throw new TRPCError({
            code: "NOT_FOUND",
            message: "Organization not found",
          });
        }
        return updated;
      } catch (error) {
        if (error instanceof TRPCError) throw error;
        if (isUniqueConstraintViolation(error)) {
          throw new TRPCError({
            code: "CONFLICT",
            message: "That organization URL is already taken",
          });
        }
        throw error;
      }
    }),

  setActiveTeam: protectedProcedure
    .input(z.object({ teamId: z.string().uuid() }))
    .mutation(async ({ ctx, input }) => {
      const userId = sessionUserId(ctx);
      const membership = await loadMembership(ctx.db, input.teamId, userId);
      if (!membership.isMember) {
        throw new TRPCError({
          code: "FORBIDDEN",
          message: "Not a member of this organization",
        });
      }
      await ctx.db
        .update(users)
        .set({ activeTeamId: input.teamId })
        .where(eq(users.id, userId));
      return { teamId: input.teamId, teamRole: membership.role };
    }),

  // ─── Membership ────────────────────────────────────────────────────────

  listMembers: protectedProcedure
    .input(z.object({ teamId: z.string().uuid() }))
    .query(async ({ ctx, input }) => {
      const userId = sessionUserId(ctx);
      await requireTeamAccess(ctx.db, input.teamId, userId, {
        platformAdmin: isPlatformAdmin(identityFromSession(ctx.session)),
        message: "Not a member of this organization",
      });
      return ctx.db
        .select({
          userId: teamMembers.userId,
          name: users.name,
          email: users.email,
          teamRole: teamMembers.teamRole,
          joinedAt: teamMembers.joinedAt,
        })
        .from(teamMembers)
        .innerJoin(users, eq(teamMembers.userId, users.id))
        .where(eq(teamMembers.teamId, input.teamId));
    }),

  removeMember: protectedProcedure
    .input(z.object({ teamId: z.string().uuid(), userId: z.string().uuid() }))
    .mutation(async ({ ctx, input }) => {
      const callerId = sessionUserId(ctx);
      const platformAdmin = isPlatformAdmin(identityFromSession(ctx.session));
      await ctx.db.transaction(async (transaction) => {
        await transaction.execute(
          sql`SELECT 1 FROM ${teams} WHERE ${teams.id} = ${input.teamId} FOR UPDATE`
        );
        const [callerMembership] = await transaction
          .select({ role: teamMembers.teamRole })
          .from(teamMembers)
          .where(
            and(
              eq(teamMembers.teamId, input.teamId),
              eq(teamMembers.userId, callerId)
            )
          )
          .limit(1);
        if (!platformAdmin && callerMembership?.role !== "owner") {
          throw new TRPCError({ code: "FORBIDDEN" });
        }

        const [target] = await transaction
          .select({ role: teamMembers.teamRole })
          .from(teamMembers)
          .where(
            and(
              eq(teamMembers.teamId, input.teamId),
              eq(teamMembers.userId, input.userId)
            )
          )
          .limit(1);
        if (!target) throw new TRPCError({ code: "NOT_FOUND" });
        if (target.role === "owner") {
          const [owners] = await transaction
            .select({ count: sql<number>`COUNT(*)::int` })
            .from(teamMembers)
            .where(
              and(
                eq(teamMembers.teamId, input.teamId),
                eq(teamMembers.teamRole, "owner")
              )
            );
          if (wouldRemoveLastOwner(target.role, null, owners?.count ?? 0)) {
            throw new TRPCError({
              code: "CONFLICT",
              message: "A team must retain at least one owner",
            });
          }
        }

        await transaction
          .delete(teamMembers)
          .where(
            and(
              eq(teamMembers.teamId, input.teamId),
              eq(teamMembers.userId, input.userId)
            )
          );
        await transaction
          .update(users)
          .set({ activeTeamId: null })
          .where(
            and(
              eq(users.id, input.userId),
              eq(users.activeTeamId, input.teamId)
            )
          );
      });
      return { success: true };
    }),

  updateMemberRole: protectedProcedure
    .input(
      z.object({
        teamId: z.string().uuid(),
        userId: z.string().uuid(),
        teamRole: z.enum(["owner", "member", "viewer"]),
      })
    )
    .mutation(async ({ ctx, input }) => {
      const callerId = sessionUserId(ctx);
      const platformAdmin = isPlatformAdmin(identityFromSession(ctx.session));
      return ctx.db.transaction(async (transaction) => {
        await transaction.execute(
          sql`SELECT 1 FROM ${teams} WHERE ${teams.id} = ${input.teamId} FOR UPDATE`
        );
        const [callerMembership] = await transaction
          .select({ role: teamMembers.teamRole })
          .from(teamMembers)
          .where(
            and(
              eq(teamMembers.teamId, input.teamId),
              eq(teamMembers.userId, callerId)
            )
          )
          .limit(1);
        if (!platformAdmin && callerMembership?.role !== "owner") {
          throw new TRPCError({ code: "FORBIDDEN" });
        }

        const [target] = await transaction
          .select({ role: teamMembers.teamRole })
          .from(teamMembers)
          .where(
            and(
              eq(teamMembers.teamId, input.teamId),
              eq(teamMembers.userId, input.userId)
            )
          )
          .limit(1);
        if (!target) throw new TRPCError({ code: "NOT_FOUND" });
        if (target.role === "owner" && input.teamRole !== "owner") {
          const [owners] = await transaction
            .select({ count: sql<number>`COUNT(*)::int` })
            .from(teamMembers)
            .where(
              and(
                eq(teamMembers.teamId, input.teamId),
                eq(teamMembers.teamRole, "owner")
              )
            );
          if (
            wouldRemoveLastOwner(
              target.role,
              input.teamRole,
              owners?.count ?? 0
            )
          ) {
            throw new TRPCError({
              code: "CONFLICT",
              message: "A team must retain at least one owner",
            });
          }
        }

        const [updated] = await transaction
          .update(teamMembers)
          .set({ teamRole: input.teamRole })
          .where(
            and(
              eq(teamMembers.teamId, input.teamId),
              eq(teamMembers.userId, input.userId)
            )
          )
          .returning();
        return updated;
      });
    }),

  leaveTeam: protectedProcedure
    .input(z.object({ teamId: z.string().uuid() }))
    .mutation(async ({ ctx, input }) => {
      const userId = sessionUserId(ctx);
      await ctx.db.transaction(async (transaction) => {
        await transaction.execute(
          sql`SELECT 1 FROM ${teams} WHERE ${teams.id} = ${input.teamId} FOR UPDATE`
        );
        const membership = await loadMembership(
          transaction,
          input.teamId,
          userId
        );
        if (!membership.isMember) {
          throw new TRPCError({
            code: "NOT_FOUND",
            message: "Not a member of this organization",
          });
        }
        if (membership.role === "owner") {
          const [owners] = await transaction
            .select({ count: sql<number>`COUNT(*)::int` })
            .from(teamMembers)
            .where(
              and(
                eq(teamMembers.teamId, input.teamId),
                eq(teamMembers.teamRole, "owner")
              )
            );
          if (wouldRemoveLastOwner("owner", null, owners?.count ?? 0)) {
            throw new TRPCError({
              code: "CONFLICT",
              message:
                "Transfer ownership before leaving: an organization must retain at least one owner",
            });
          }
        }

        await transaction
          .delete(teamMembers)
          .where(
            and(
              eq(teamMembers.teamId, input.teamId),
              eq(teamMembers.userId, userId)
            )
          );
        await transaction
          .update(users)
          .set({ activeTeamId: null })
          .where(
            and(eq(users.id, userId), eq(users.activeTeamId, input.teamId))
          );
      });
      return { success: true };
    }),

  // ─── Invitations ───────────────────────────────────────────────────────

  createInvitation: protectedProcedure
    .input(
      z.object({
        teamId: z.string().uuid(),
        email: z.string().trim().toLowerCase().email().max(254),
        teamRole: delegableRoleSchema.default("member"),
        // TODO: flip to `false` once the dashboard's one-time-reveal UI stops
        // assuming the accept link is always present in the response.
        returnLink: z.boolean().default(true),
      })
    )
    .mutation(async ({ ctx, input }) =>
      issueInvitation(ctx, {
        teamId: input.teamId,
        email: input.email,
        teamRole: input.teamRole,
        returnLink: input.returnLink,
      })
    ),

  /** @deprecated Consent-free membership insertion is gone; this now invites. */
  inviteMember: protectedProcedure
    .input(
      z.object({
        teamId: z.string().uuid(),
        userId: z.string().uuid(),
        teamRole: delegableRoleSchema.default("member"),
        returnLink: z.boolean().default(true),
      })
    )
    .mutation(async ({ ctx, input }) => {
      // Authorize against the organization before the target user is resolved:
      // resolving first let an authenticated outsider tell "real user" from
      // "not your organization" by the error code alone.
      await requireTeamAccess(ctx.db, input.teamId, sessionUserId(ctx), {
        platformAdmin: isPlatformAdmin(identityFromSession(ctx.session)),
        allow: canManageInvitations,
        message: "Not authorized to invite people to this organization",
      });
      const [target] = await ctx.db
        .select({ email: users.email })
        .from(users)
        .where(eq(users.id, input.userId))
        .limit(1);
      if (!target?.email) {
        throw new TRPCError({ code: "NOT_FOUND", message: "User not found" });
      }
      return issueInvitation(ctx, {
        teamId: input.teamId,
        email: target.email,
        teamRole: input.teamRole,
        returnLink: input.returnLink,
      });
    }),

  listInvitations: protectedProcedure
    .input(z.object({ teamId: z.string().uuid() }))
    .query(async ({ ctx, input }) => {
      const userId = sessionUserId(ctx);
      await requireTeamAccess(ctx.db, input.teamId, userId, {
        platformAdmin: isPlatformAdmin(identityFromSession(ctx.session)),
        allow: canManageInvitations,
        message: "Not authorized to view invitations",
      });
      const rows = await ctx.db
        .select({
          id: teamInvitations.id,
          email: teamInvitations.email,
          teamRole: teamInvitations.teamRole,
          invitedBy: teamInvitations.invitedBy,
          expiresAt: teamInvitations.expiresAt,
          acceptedAt: teamInvitations.acceptedAt,
          revokedAt: teamInvitations.revokedAt,
          createdAt: teamInvitations.createdAt,
        })
        .from(teamInvitations)
        .where(
          and(
            eq(teamInvitations.teamId, input.teamId),
            isNull(teamInvitations.acceptedAt),
            isNull(teamInvitations.revokedAt)
          )
        );
      const now = new Date();
      // Never project tokenHash: pending invitations are credentials.
      return rows
        .map((row) => ({
          id: row.id,
          email: row.email,
          teamRole: resolveTeamRole(row.teamRole),
          invitedBy: row.invitedBy,
          expiresAt: row.expiresAt,
          createdAt: row.createdAt,
          state: invitationState(row, now),
        }))
        .filter((row) => row.state === "pending");
    }),

  revokeInvitation: protectedProcedure
    .input(z.object({ invitationId: z.string().uuid() }))
    .mutation(async ({ ctx, input }) => {
      const userId = sessionUserId(ctx);
      const [invitation] = await ctx.db
        .select({
          id: teamInvitations.id,
          teamId: teamInvitations.teamId,
          acceptedAt: teamInvitations.acceptedAt,
        })
        .from(teamInvitations)
        .where(eq(teamInvitations.id, input.invitationId))
        .limit(1);
      if (!invitation) {
        throw new TRPCError({ code: "NOT_FOUND", message: "Invitation not found" });
      }
      await requireTeamAccess(ctx.db, invitation.teamId, userId, {
        platformAdmin: isPlatformAdmin(identityFromSession(ctx.session)),
        allow: canManageInvitations,
        message: "Not authorized to revoke invitations",
      });
      if (invitation.acceptedAt) {
        throw new TRPCError({
          code: "PRECONDITION_FAILED",
          message: invitationRejectionMessage("accepted"),
        });
      }
      await ctx.db
        .update(teamInvitations)
        .set({ revokedAt: new Date() })
        .where(
          and(
            eq(teamInvitations.id, invitation.id),
            isNull(teamInvitations.revokedAt),
            isNull(teamInvitations.acceptedAt)
          )
        );
      return { success: true };
    }),

  previewInvitation: publicProcedure
    .input(z.object({ token: secretSchema }))
    .query(async ({ ctx, input }): Promise<InvitationPreview> => {
      const invalid: InvitationPreview = { valid: false };
      const tokenHash = hashToken(input.token);
      const [row] = await ctx.db
        .select({
          tokenHash: teamInvitations.tokenHash,
          email: teamInvitations.email,
          teamRole: teamInvitations.teamRole,
          expiresAt: teamInvitations.expiresAt,
          acceptedAt: teamInvitations.acceptedAt,
          revokedAt: teamInvitations.revokedAt,
          orgName: teams.name,
        })
        .from(teamInvitations)
        .innerJoin(teams, eq(teamInvitations.teamId, teams.id))
        .where(eq(teamInvitations.tokenHash, tokenHash))
        .limit(1);
      if (!row || !timingSafeTokenEqual(row.tokenHash, tokenHash)) return invalid;
      if (invitationState(row) !== "pending") return invalid;
      const role = resolveTeamRole(row.teamRole);
      if (!role) return invalid;
      return {
        valid: true,
        orgName: row.orgName,
        email: row.email,
        role,
      };
    }),

  acceptInvitation: protectedProcedure
    .input(z.object({ token: secretSchema }))
    .mutation(async ({ ctx, input }): Promise<MembershipResult> => {
      const userId = sessionUserId(ctx);
      const tokenHash = hashToken(input.token);

      return ctx.db.transaction(async (transaction) => {
        const [invitation] = await transaction
          .select({
            id: teamInvitations.id,
            teamId: teamInvitations.teamId,
            email: teamInvitations.email,
            teamRole: teamInvitations.teamRole,
            tokenHash: teamInvitations.tokenHash,
            expiresAt: teamInvitations.expiresAt,
            acceptedAt: teamInvitations.acceptedAt,
            acceptedBy: teamInvitations.acceptedBy,
            revokedAt: teamInvitations.revokedAt,
          })
          .from(teamInvitations)
          .where(eq(teamInvitations.tokenHash, tokenHash))
          .limit(1)
          .for("update");
        if (!invitation || !timingSafeTokenEqual(invitation.tokenHash, tokenHash)) {
          throw new TRPCError({
            code: "NOT_FOUND",
            message: "This invitation is no longer valid",
          });
        }

        // The invited address is the whole authorization: it must come from the
        // database inside this transaction and the account must have proved it
        // controls the mailbox, or a forwarded link is enough to claim a seat.
        const email = requireVerifiedIdentity(
          await loadRedeemingIdentity(transaction, userId)
        );
        if (!emailMatchesInvitation(invitation.email, email)) {
          throw new TRPCError({
            code: "FORBIDDEN",
            message: "This invitation was sent to a different email address",
          });
        }

        const org = await loadOrganization(transaction, invitation.teamId);
        const membership = await loadMembership(
          transaction,
          invitation.teamId,
          userId
        );
        const state = invitationState(invitation);

        if (membership.isMember) {
          // Idempotent: a second click must not fail or duplicate membership.
          if (state === "pending") {
            await transaction
              .update(teamInvitations)
              .set({ acceptedAt: new Date(), acceptedBy: userId })
              .where(
                and(
                  eq(teamInvitations.id, invitation.id),
                  isNull(teamInvitations.acceptedAt)
                )
              );
          }
          return {
            teamId: invitation.teamId,
            teamRole: membership.role ?? "viewer",
            orgName: org.name,
            orgSlug: org.slug,
            alreadyMember: true,
          };
        }

        if (state !== "pending") {
          throw new TRPCError({
            code: "PRECONDITION_FAILED",
            message: invitationRejectionMessage(state),
          });
        }

        const claimed = await transaction
          .update(teamInvitations)
          .set({ acceptedAt: new Date(), acceptedBy: userId })
          .where(
            and(
              eq(teamInvitations.id, invitation.id),
              isNull(teamInvitations.acceptedAt),
              isNull(teamInvitations.revokedAt)
            )
          )
          .returning({ id: teamInvitations.id });
        if (claimed.length === 0) {
          throw new TRPCError({
            code: "CONFLICT",
            message: invitationRejectionMessage("accepted"),
          });
        }

        // Clamped, not merely narrowed: a stored `owner` on an invitation row
        // must never become a one-click ownership grant.
        const teamRole = resolveDelegableRole(invitation.teamRole);
        await transaction.insert(teamMembers).values({
          teamId: invitation.teamId,
          userId,
          teamRole,
        });
        await adoptActiveTeam(transaction, userId, invitation.teamId);

        return {
          teamId: invitation.teamId,
          teamRole,
          orgName: org.name,
          orgSlug: org.slug,
          alreadyMember: false,
        };
      });
    }),

  // ─── Join links ────────────────────────────────────────────────────────

  createJoinLink: protectedProcedure
    .input(
      z.object({
        teamId: z.string().uuid(),
        teamRole: delegableRoleSchema.default("viewer"),
        allowedEmailDomain: z.string().trim().max(253).optional().nullable(),
        maxUses: z.number().int().min(1).max(10_000).optional().nullable(),
        expiresAt: z.date().optional().nullable(),
      })
    )
    .mutation(async ({ ctx, input }) => {
      const userId = sessionUserId(ctx);
      await requireTeamAccess(ctx.db, input.teamId, userId, {
        platformAdmin: isPlatformAdmin(identityFromSession(ctx.session)),
        allow: canCreateJoinLink,
        message: "Only an owner can create a join link",
      });

      const allowedEmailDomain = input.allowedEmailDomain
        ? normalizeEmailDomain(input.allowedEmailDomain)
        : null;
      if (input.allowedEmailDomain && !allowedEmailDomain) {
        throw new TRPCError({
          code: "BAD_REQUEST",
          message: "That email domain is not valid",
        });
      }
      if (input.expiresAt) {
        const ttl = input.expiresAt.getTime() - Date.now();
        if (ttl <= 0 || ttl > MAX_JOIN_LINK_TTL_MS) {
          throw new TRPCError({
            code: "BAD_REQUEST",
            message: "Expiry must be in the future and within one year",
          });
        }
      }

      const { token, tokenHash } = generateToken();
      const [link] = await ctx.db
        .insert(teamJoinLinks)
        .values({
          teamId: input.teamId,
          codeHash: tokenHash,
          teamRole: input.teamRole,
          allowedEmailDomain,
          maxUses: input.maxUses ?? null,
          expiresAt: input.expiresAt ?? null,
          createdBy: userId,
        })
        .returning({
          id: teamJoinLinks.id,
          teamRole: teamJoinLinks.teamRole,
          allowedEmailDomain: teamJoinLinks.allowedEmailDomain,
          maxUses: teamJoinLinks.maxUses,
          expiresAt: teamJoinLinks.expiresAt,
        });

      // The raw code is returned exactly once; only its hash is stored.
      return {
        id: link.id,
        code: token,
        joinUrl: joinLinkUrl(token),
        teamRole: resolveTeamRole(link.teamRole),
        allowedEmailDomain: link.allowedEmailDomain,
        maxUses: link.maxUses,
        expiresAt: link.expiresAt,
      };
    }),

  listJoinLinks: protectedProcedure
    .input(z.object({ teamId: z.string().uuid() }))
    .query(async ({ ctx, input }) => {
      const userId = sessionUserId(ctx);
      await requireTeamAccess(ctx.db, input.teamId, userId, {
        platformAdmin: isPlatformAdmin(identityFromSession(ctx.session)),
        allow: canManageInvitations,
        message: "Not authorized to view join links",
      });
      const rows = await ctx.db
        .select({
          id: teamJoinLinks.id,
          teamRole: teamJoinLinks.teamRole,
          allowedEmailDomain: teamJoinLinks.allowedEmailDomain,
          maxUses: teamJoinLinks.maxUses,
          useCount: teamJoinLinks.useCount,
          expiresAt: teamJoinLinks.expiresAt,
          revokedAt: teamJoinLinks.revokedAt,
          createdBy: teamJoinLinks.createdBy,
          createdAt: teamJoinLinks.createdAt,
        })
        .from(teamJoinLinks)
        .where(eq(teamJoinLinks.teamId, input.teamId));
      const now = new Date();
      // Metadata only: neither the raw code nor its hash ever leaves the server.
      return rows.map((row) => ({
        id: row.id,
        teamRole: resolveTeamRole(row.teamRole),
        allowedEmailDomain: row.allowedEmailDomain,
        maxUses: row.maxUses,
        useCount: row.useCount,
        expiresAt: row.expiresAt,
        revokedAt: row.revokedAt,
        createdBy: row.createdBy,
        createdAt: row.createdAt,
        state: joinLinkState(row, now),
      }));
    }),

  rotateJoinLink: protectedProcedure
    .input(z.object({ linkId: z.string().uuid() }))
    .mutation(async ({ ctx, input }) => {
      const userId = sessionUserId(ctx);
      const platformAdmin = isPlatformAdmin(identityFromSession(ctx.session));
      const [existing] = await ctx.db
        .select({
          id: teamJoinLinks.id,
          teamId: teamJoinLinks.teamId,
          teamRole: teamJoinLinks.teamRole,
          allowedEmailDomain: teamJoinLinks.allowedEmailDomain,
          maxUses: teamJoinLinks.maxUses,
          expiresAt: teamJoinLinks.expiresAt,
        })
        .from(teamJoinLinks)
        .where(eq(teamJoinLinks.id, input.linkId))
        .limit(1);
      if (!existing) {
        throw new TRPCError({ code: "NOT_FOUND", message: "Join link not found" });
      }
      await requireTeamAccess(ctx.db, existing.teamId, userId, {
        platformAdmin,
        allow: canRevokeJoinLink,
        message: "Only an owner can rotate a join link",
      });

      const { token, tokenHash } = generateToken();
      const replacement = await ctx.db.transaction(async (transaction) => {
        await transaction
          .update(teamJoinLinks)
          .set({ revokedAt: new Date() })
          .where(
            and(
              eq(teamJoinLinks.id, existing.id),
              isNull(teamJoinLinks.revokedAt)
            )
          );
        const [created] = await transaction
          .insert(teamJoinLinks)
          .values({
            teamId: existing.teamId,
            codeHash: tokenHash,
            // Rotation re-validates the copied role instead of trusting the row.
            teamRole: resolveDelegableRole(existing.teamRole),
            allowedEmailDomain: existing.allowedEmailDomain,
            maxUses: existing.maxUses,
            expiresAt: existing.expiresAt,
            createdBy: userId,
          })
          .returning({
            id: teamJoinLinks.id,
            teamRole: teamJoinLinks.teamRole,
            allowedEmailDomain: teamJoinLinks.allowedEmailDomain,
            maxUses: teamJoinLinks.maxUses,
            expiresAt: teamJoinLinks.expiresAt,
          });
        return created;
      });

      return {
        id: replacement.id,
        code: token,
        joinUrl: joinLinkUrl(token),
        teamRole: resolveTeamRole(replacement.teamRole),
        allowedEmailDomain: replacement.allowedEmailDomain,
        maxUses: replacement.maxUses,
        expiresAt: replacement.expiresAt,
        revokedLinkId: existing.id,
      };
    }),

  revokeJoinLink: protectedProcedure
    .input(z.object({ linkId: z.string().uuid() }))
    .mutation(async ({ ctx, input }) => {
      const userId = sessionUserId(ctx);
      const [existing] = await ctx.db
        .select({ id: teamJoinLinks.id, teamId: teamJoinLinks.teamId })
        .from(teamJoinLinks)
        .where(eq(teamJoinLinks.id, input.linkId))
        .limit(1);
      if (!existing) {
        throw new TRPCError({ code: "NOT_FOUND", message: "Join link not found" });
      }
      await requireTeamAccess(ctx.db, existing.teamId, userId, {
        platformAdmin: isPlatformAdmin(identityFromSession(ctx.session)),
        allow: canRevokeJoinLink,
        message: "Only an owner can revoke a join link",
      });
      await ctx.db
        .update(teamJoinLinks)
        .set({ revokedAt: new Date() })
        .where(
          and(
            eq(teamJoinLinks.id, existing.id),
            isNull(teamJoinLinks.revokedAt)
          )
        );
      return { success: true };
    }),

  previewJoinLink: publicProcedure
    .input(z.object({ code: secretSchema }))
    .query(async ({ ctx, input }): Promise<JoinLinkPreview> => {
      const invalid: JoinLinkPreview = { valid: false };
      const codeHash = hashToken(input.code);
      const [row] = await ctx.db
        .select({
          codeHash: teamJoinLinks.codeHash,
          teamRole: teamJoinLinks.teamRole,
          maxUses: teamJoinLinks.maxUses,
          useCount: teamJoinLinks.useCount,
          expiresAt: teamJoinLinks.expiresAt,
          revokedAt: teamJoinLinks.revokedAt,
          orgName: teams.name,
        })
        .from(teamJoinLinks)
        .innerJoin(teams, eq(teamJoinLinks.teamId, teams.id))
        .where(eq(teamJoinLinks.codeHash, codeHash))
        .limit(1);
      if (!row || !timingSafeTokenEqual(row.codeHash, codeHash)) return invalid;
      if (joinLinkState(row) !== "active") return invalid;
      const role = resolveTeamRole(row.teamRole);
      if (!role) return invalid;
      return { valid: true, orgName: row.orgName, role };
    }),

  redeemJoinLink: protectedProcedure
    .input(z.object({ code: secretSchema }))
    .mutation(async ({ ctx, input }): Promise<MembershipResult> => {
      const userId = sessionUserId(ctx);
      const codeHash = hashToken(input.code);

      return ctx.db.transaction(async (transaction) => {
        const [link] = await transaction
          .select({
            id: teamJoinLinks.id,
            teamId: teamJoinLinks.teamId,
            codeHash: teamJoinLinks.codeHash,
            teamRole: teamJoinLinks.teamRole,
            allowedEmailDomain: teamJoinLinks.allowedEmailDomain,
            maxUses: teamJoinLinks.maxUses,
            useCount: teamJoinLinks.useCount,
            expiresAt: teamJoinLinks.expiresAt,
            revokedAt: teamJoinLinks.revokedAt,
          })
          .from(teamJoinLinks)
          .where(eq(teamJoinLinks.codeHash, codeHash))
          .limit(1)
          .for("update");
        if (!link || !timingSafeTokenEqual(link.codeHash, codeHash)) {
          throw new TRPCError({
            code: "NOT_FOUND",
            message: "This join link is no longer valid",
          });
        }

        // Read inside the transaction: a domain allowlist is admission control
        // and the JWT email claim is neither fresh nor proof of mailbox control.
        const identity = await loadRedeemingIdentity(transaction, userId);

        const org = await loadOrganization(transaction, link.teamId);
        const membership = await loadMembership(transaction, link.teamId, userId);
        if (membership.isMember) {
          // Idempotent: re-opening the link must not burn a use.
          return {
            teamId: link.teamId,
            teamRole: membership.role ?? "viewer",
            orgName: org.name,
            orgSlug: org.slug,
            alreadyMember: true,
          };
        }

        const now = new Date();
        const decision = canRedeemJoinLink(link, now, identity);
        if (!decision.allowed) {
          throw new TRPCError({
            code:
              decision.reason === "domain_mismatch" ||
              decision.reason === "missing_email" ||
              decision.reason === "unverified_email"
                ? "FORBIDDEN"
                : "PRECONDITION_FAILED",
            message: joinLinkRejectionMessage(decision.reason),
          });
        }

        // Conditional increment: the use budget is re-checked inside the write,
        // so concurrent redeems can never push use_count past max_uses.
        const claimed = await transaction
          .update(teamJoinLinks)
          .set({ useCount: sql`${teamJoinLinks.useCount} + 1` })
          .where(joinLinkClaimCondition(link.id, now))
          .returning({ useCount: teamJoinLinks.useCount });
        if (claimed.length === 0) {
          throw new TRPCError({
            code: "PRECONDITION_FAILED",
            message: joinLinkRejectionMessage("exhausted"),
          });
        }

        // Clamped, not merely narrowed: see `acceptInvitation`.
        const teamRole = resolveDelegableRole(link.teamRole);
        await transaction.insert(teamMembers).values({
          teamId: link.teamId,
          userId,
          teamRole,
        });
        await adoptActiveTeam(transaction, userId, link.teamId);

        return {
          teamId: link.teamId,
          teamRole,
          orgName: org.name,
          orgSlug: org.slug,
          alreadyMember: false,
        };
      });
    }),

  // ─── Directory and dashboards ──────────────────────────────────────────

  getTeamsInBbox: protectedProcedure
    .input(z.object({ bbox: z.string() }))
    .query(() => {
      throw new TRPCError({
        code: "PRECONDITION_FAILED",
        message: PARTNER_DIRECTORY_UNAVAILABLE_MESSAGE,
      });
    }),

  getTeamProfile: protectedProcedure
    .input(z.object({ id: z.string().uuid() }))
    .query(async ({ ctx, input }) => {
      const userId = sessionUserId(ctx);
      const [membership] = await ctx.db
        .select({ teamId: teamMembers.teamId })
        .from(teamMembers)
        .where(
          and(
            eq(teamMembers.teamId, input.id),
            eq(teamMembers.userId, userId)
          )
        )
        .limit(1);
      if (!membership) {
        throw new TRPCError({ code: "FORBIDDEN", message: "Not a team member" });
      }

      const [team] = await ctx.db
        .select({
          id: teams.id,
          name: teams.name,
          slug: teams.slug,
          description: teams.description,
          orgType: teams.orgType,
          specialties: teams.specialties,
          website: teams.website,
          serviceArea: teams.serviceArea,
          isVerified: teams.isVerified,
          createdAt: teams.createdAt,
        })
        .from(teams)
        .where(eq(teams.id, input.id))
        .limit(1);
      if (!team) {
        throw new TRPCError({
          code: "NOT_FOUND",
          message: "Organization not found",
        });
      }

      const members = await ctx.db
        .select({
          teamRole: teamMembers.teamRole,
          name: users.name,
        })
        .from(teamMembers)
        .innerJoin(users, eq(teamMembers.userId, users.id))
        .where(eq(teamMembers.teamId, input.id));

      return { ...team, members, memberCount: members.length };
    }),

  getTeamDashboard: protectedProcedure
    .input(z.object({ teamId: z.string().uuid() }))
    .query(async ({ ctx, input }) => {
      const userId = sessionUserId(ctx);
      const [membership] = await ctx.db
        .select()
        .from(teamMembers)
        .where(
          and(
            eq(teamMembers.teamId, input.teamId),
            eq(teamMembers.userId, userId)
          )
        )
        .limit(1);
      if (!membership) {
        throw new TRPCError({
          code: "FORBIDDEN",
          message: "Not a member of this organization",
        });
      }

      const [team] = await ctx.db
        .select({
          id: teams.id,
          name: teams.name,
          slug: teams.slug,
          description: teams.description,
          orgType: teams.orgType,
          specialties: teams.specialties,
          website: teams.website,
          serviceArea: teams.serviceArea,
          isVerified: teams.isVerified,
          createdAt: teams.createdAt,
        })
        .from(teams)
        .where(eq(teams.id, input.teamId))
        .limit(1);
      if (!team) {
        throw new TRPCError({
          code: "NOT_FOUND",
          message: "Organization not found",
        });
      }

      const members = await ctx.db
        .select({
          teamRole: teamMembers.teamRole,
          name: users.name,
        })
        .from(teamMembers)
        .innerJoin(users, eq(teamMembers.userId, users.id))
        .where(eq(teamMembers.teamId, input.teamId));

      // Opportunity waypoints stay inactive until reviewed publication is available.
      return {
        team,
        members,
        memberRole: membership.teamRole,
        priorityZones: [] as Array<{
          id: string;
          strategyType: string;
          requestCount: number;
          totalVotes: number;
        }>,
        opportunityWaypoints: {
          state: "inactive" as const,
          message: OPPORTUNITY_WAYPOINTS_UNAVAILABLE_MESSAGE,
        },
      };
    }),

  // ─── Phase 6: Admin verification ──────────────────────────────────────

  verifyTeam: adminProcedure
    .input(z.object({ teamId: z.string().uuid() }))
    .mutation(async ({ ctx, input }) => {
      const [updated] = await ctx.db
        .update(teams)
        .set({ isVerified: true, verifiedAt: new Date() })
        .where(eq(teams.id, input.teamId))
        .returning();
      if (!updated) {
        throw new TRPCError({
          code: "NOT_FOUND",
          message: "Organization not found",
        });
      }
      return updated;
    }),
});
