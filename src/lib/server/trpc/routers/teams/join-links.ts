import { z } from "zod";
import { TRPCError } from "@trpc/server";
import { and, eq, isNull, or, sql, type SQL } from "drizzle-orm";
import { protectedProcedure, publicProcedure } from "@/lib/server/trpc/init";
import { teamJoinLinks, teamMembers, teams } from "@/lib/server/db/schema";
import {
  canCreateJoinLink,
  canManageInvitations,
  canRevokeJoinLink,
  identityFromSession,
  isPlatformAdmin,
  resolveDelegableRole,
  resolveTeamRole,
  type TeamRole,
} from "@/lib/server/security/access-control";
import {
  canRedeemJoinLink,
  joinLinkRejectionMessage,
  joinLinkState,
  normalizeEmailDomain,
} from "@/lib/server/security/invitations";
import {
  generateToken,
  hashToken,
  timingSafeTokenEqual,
} from "@/lib/server/security/tokens";
import { appUrl } from "@/lib/server/services/transactional-email";
import {
  adoptActiveTeam,
  delegableRoleSchema,
  loadMembership,
  loadOrganization,
  loadRedeemingIdentity,
  requireTeamAccess,
  secretSchema,
  sessionUserId,
  type MembershipResult,
} from "./shared";

const MAX_JOIN_LINK_TTL_MS = 365 * 24 * 60 * 60 * 1000;

function joinLinkUrl(code: string): string {
  return `${appUrl()}/api/join/${encodeURIComponent(code)}`;
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

type JoinLinkPreview =
  | { valid: true; orgName: string; role: TeamRole }
  | { valid: false; orgName?: undefined; role?: undefined };

/** Self-serve join links: create/list/rotate/revoke and redemption. */
export const joinLinkProcedures = {
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
};
