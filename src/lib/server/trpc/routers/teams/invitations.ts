import { z } from "zod";
import { TRPCError } from "@trpc/server";
import { and, eq, isNull, sql } from "drizzle-orm";
import {
  protectedProcedure,
  publicProcedure,
  type Context,
} from "@/lib/server/trpc/init";
import { teamInvitations, teamMembers, teams, users } from "@/lib/server/db/schema";
import {
  canInviteTeamRole,
  canManageInvitations,
  identityFromSession,
  isPlatformAdmin,
  resolveDelegableRole,
  resolveTeamRole,
  type TeamRole,
} from "@/lib/server/security/access-control";
import {
  emailMatchesInvitation,
  INVITATIONS_PER_MINUTE,
  invitationRateLimitKey,
  invitationRejectionMessage,
  invitationState,
  normalizeEmail,
} from "@/lib/server/security/invitations";
import { checkRateLimit } from "@/lib/server/middleware/api-auth";
import {
  generateToken,
  hashToken,
  timingSafeTokenEqual,
  tokenExpiry,
  TOKEN_TTL,
} from "@/lib/server/security/tokens";
import { appUrl, sendOrgInvitation } from "@/lib/server/services/transactional-email";
import {
  adoptActiveTeam,
  delegableRoleSchema,
  loadMembership,
  loadOrganization,
  loadRedeemingIdentity,
  requireTeamAccess,
  requireVerifiedIdentity,
  secretSchema,
  sessionDisplayName,
  sessionUserId,
  type Database,
  type MembershipResult,
} from "./shared";

function invitationAcceptUrl(token: string): string {
  return `${appUrl()}/api/invitations/${encodeURIComponent(token)}`;
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

/** Membership, invitation and join-link CRUD for organizations. */
export const invitationProcedures = {
  createInvitation: protectedProcedure
    .input(
      z.object({
        teamId: z.string().uuid(),
        email: z.string().trim().toLowerCase().email().max(254),
        teamRole: delegableRoleSchema.default("member"),
        // Opt-in since 2026-09-02: the accept link is a live credential, so a caller that
        // does not render a one-time reveal must not receive one. The invitee still gets it
        // by email either way. `dashboard/org/invitations` asks for it explicitly.
        returnLink: z.boolean().default(false),
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

  /**
   * @deprecated Use `createInvitation`, which takes the invitee's email directly. Consent-free
   * membership insertion is gone; this shim only resolves `userId` to an email and then calls
   * the same `issueInvitation`. Sunset 2026-10-01: after that date this procedure and its
   * compatibility test in `src/__tests__/security/org-invitations.test.ts` are removed. It is
   * retained until then only because no production consumer telemetry exists to prove that
   * nothing outside this repository still calls it -- see the conformity `c3` proof packet.
   */
  inviteMember: protectedProcedure
    .input(
      z.object({
        teamId: z.string().uuid(),
        userId: z.string().uuid(),
        teamRole: delegableRoleSchema.default("member"),
        /** Same opt-in default as `createInvitation`; see the note there. */
        returnLink: z.boolean().default(false),
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
};
