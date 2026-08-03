import { DrizzleAdapter } from "@auth/drizzle-adapter";
import type { NextAuthOptions } from "next-auth";
import Credentials from "next-auth/providers/credentials";
import GitHub from "next-auth/providers/github";
import Google from "next-auth/providers/google";
import { and, asc, eq, isNull } from "drizzle-orm";
import { db } from "@/lib/server/db";
import { teamMembers, users } from "@/lib/server/db/schema";
import { authAdapterTables } from "@/lib/server/auth-schema";
import { verifyPassword } from "@/lib/server/password";
import { normalizeAuthEmail } from "@/lib/server/security/registration";
import type { ActiveTeamRole } from "@/types/next-auth";

const providers: NextAuthOptions["providers"] = [
  Credentials({
    name: "credentials",
    credentials: {
      email: { label: "Email", type: "email" },
      password: { label: "Password", type: "password" },
    },
    async authorize(credentials) {
      if (!credentials?.email || !credentials?.password) return null;

      // Registration stores the lowercased address, so an exact match would
      // lock out anyone who types their address with different casing.
      const email = normalizeAuthEmail(credentials.email);
      if (email.length === 0) return null;

      const [user] = await db
        .select()
        .from(users)
        .where(eq(users.email, email))
        .limit(1);
      if (!user?.passwordHash) return null;

      const valid = await verifyPassword(credentials.password, user.passwordHash);
      if (!valid) return null;

      return {
        id: user.id,
        email: user.email,
        name: user.name,
        platformRole: user.platformRole ?? "contributor",
      };
    },
  }),
];

const googleClientId = process.env.GOOGLE_CLIENT_ID?.trim();
const googleClientSecret = process.env.GOOGLE_CLIENT_SECRET?.trim();
if (googleClientId && googleClientSecret) {
  providers.push(
    Google({ clientId: googleClientId, clientSecret: googleClientSecret })
  );
}

const githubClientId = process.env.GITHUB_CLIENT_ID?.trim();
const githubClientSecret = process.env.GITHUB_CLIENT_SECRET?.trim();
if (githubClientId && githubClientSecret) {
  providers.push(
    GitHub({ clientId: githubClientId, clientSecret: githubClientSecret })
  );
}

const TEAM_ROLES: readonly ActiveTeamRole[] = ["owner", "member", "viewer"];

/** Membership rows carry a free-form varchar; unknown values degrade to member. */
function normalizeTeamRole(role: string | null): ActiveTeamRole {
  return TEAM_ROLES.includes(role as ActiveTeamRole)
    ? (role as ActiveTeamRole)
    : "member";
}

/**
 * Whether the provider itself vouches for the address on this account.
 *
 * An explicit allowlist, never a blanket "any OAuth provider": `emailVerified`
 * is an authorization input for invitation acceptance, so an unverified claim
 * must not be able to launder itself into one.
 *
 * - google: the OIDC `email_verified` claim, asserted per address.
 * - github: verification is only exposed on /user/emails, which next-auth's
 *   provider reads to pick the *primary* address without inspecting its
 *   `verified` flag — so no claim survives onto the profile. This trusts
 *   GitHub's primary address; tightening it needs a custom `profile()` that
 *   filters on `verified`.
 */
function providerAssertsVerifiedEmail(
  provider: string,
  profile: unknown
): boolean {
  if (provider === "google") {
    const claim = (profile as { email_verified?: unknown } | null)
      ?.email_verified;
    return claim === true || claim === "true";
  }
  return provider === "github";
}

/**
 * The Drizzle adapter never writes `emailVerified`, so an OAuth account would
 * stay unverified forever. Stamp it once, and only while the column is still
 * null: a credential account must remain unverified until it redeems its
 * emailed link, and an existing timestamp is never rewritten.
 *
 * Best-effort — a failure here must not deny an otherwise valid sign-in.
 */
async function stampProviderVerifiedEmail(userId: string): Promise<void> {
  try {
    await db
      .update(users)
      .set({ emailVerified: new Date() })
      .where(and(eq(users.id, userId), isNull(users.emailVerified)));
  } catch (error) {
    console.error("[auth] Failed to stamp provider-verified email", error);
  }
}

interface OrganizationContext {
  platformRole: string;
  activeTeamId: string | null;
  activeTeamRole: ActiveTeamRole | null;
}

/**
 * One round trip per request: the identity row plus every membership, joined.
 * Returns null when the user no longer exists, which revokes the session.
 */
async function loadOrganizationContext(
  userId: string,
  requestedTeamId: string | null
): Promise<OrganizationContext | null> {
  const rows = await db
    .select({
      platformRole: users.platformRole,
      activeTeamId: users.activeTeamId,
      memberTeamId: teamMembers.teamId,
      memberRole: teamMembers.teamRole,
    })
    .from(users)
    .leftJoin(teamMembers, eq(teamMembers.userId, users.id))
    .where(eq(users.id, userId))
    .orderBy(asc(teamMembers.joinedAt), asc(teamMembers.teamId));

  if (!Array.isArray(rows) || rows.length === 0) return null;

  const identity = rows[0];
  const memberships = rows.filter(
    (row): row is (typeof rows)[number] & { memberTeamId: string } =>
      typeof row.memberTeamId === "string"
  );

  // Preference order: an explicit update request, the stored active team, then
  // the earliest joined membership. Any choice must still be an active membership.
  const preferred =
    (requestedTeamId
      ? memberships.find((row) => row.memberTeamId === requestedTeamId)
      : undefined) ??
    (identity.activeTeamId
      ? memberships.find((row) => row.memberTeamId === identity.activeTeamId)
      : undefined) ??
    memberships[0] ??
    null;

  return {
    platformRole: identity.platformRole ?? "contributor",
    activeTeamId: preferred?.memberTeamId ?? null,
    activeTeamRole: preferred ? normalizeTeamRole(preferred.memberRole) : null,
  };
}

/** Shared server-side NextAuth configuration for routes and session consumers. */
export const authOptions: NextAuthOptions = {
  adapter: DrizzleAdapter(db, authAdapterTables),
  session: { strategy: "jwt" },
  providers,
  callbacks: {
    async jwt({ token, user, account, profile, trigger, session }) {
      const userId = user?.id ?? token.id ?? token.sub;
      if (typeof userId !== "string") {
        throw new Error("Session identity is unavailable");
      }

      // The only hook that sees both the raw provider profile and the row the
      // adapter just created; `account` is absent on every later refresh.
      if (
        account?.type === "oauth" &&
        providerAssertsVerifiedEmail(account.provider, profile)
      ) {
        await stampProviderVerifiedEmail(userId);
      }

      const update = trigger === "update" ? (session as unknown) : null;
      const requestedTeamId =
        update && typeof update === "object" && "activeTeamId" in update &&
        typeof (update as { activeTeamId?: unknown }).activeTeamId === "string"
          ? ((update as { activeTeamId: string }).activeTeamId)
          : null;

      const context = await loadOrganizationContext(userId, requestedTeamId);
      if (!context) throw new Error("Session identity is no longer active");

      token.id = userId;
      token.platformRole = context.platformRole;
      token.activeTeamId = context.activeTeamId;
      token.activeTeamRole = context.activeTeamRole;
      return token;
    },
    async session({ session, token }) {
      if (
        !session.user ||
        typeof token.id !== "string" ||
        typeof token.platformRole !== "string"
      ) {
        throw new Error("Session identity is unavailable");
      }
      session.user.id = token.id;
      session.user.platformRole = token.platformRole;
      session.user.activeTeamId = token.activeTeamId ?? null;
      session.user.activeTeamRole = token.activeTeamRole ?? null;
      return session;
    },
  },
  pages: {
    signIn: "/login",
  },
};
