import { DrizzleAdapter } from "@auth/drizzle-adapter";
import type { NextAuthOptions } from "next-auth";
import Credentials from "next-auth/providers/credentials";
import GitHub from "next-auth/providers/github";
import Google from "next-auth/providers/google";
import { eq } from "drizzle-orm";
import { db } from "@/lib/server/db";
import { users } from "@/lib/server/db/schema";
import { authAdapterTables } from "@/lib/server/auth-schema";
import { verifyPassword } from "@/lib/server/password";

const providers: NextAuthOptions["providers"] = [
  Credentials({
    name: "credentials",
    credentials: {
      email: { label: "Email", type: "email" },
      password: { label: "Password", type: "password" },
    },
    async authorize(credentials) {
      if (!credentials?.email || !credentials?.password) return null;

      const [user] = await db
        .select()
        .from(users)
        .where(eq(users.email, credentials.email))
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

/** Shared server-side NextAuth configuration for routes and session consumers. */
export const authOptions: NextAuthOptions = {
  adapter: DrizzleAdapter(db, authAdapterTables),
  session: { strategy: "jwt" },
  providers,
  callbacks: {
    async jwt({ token, user }) {
      const userId = user?.id ?? token.id ?? token.sub;
      if (typeof userId !== "string") {
        throw new Error("Session identity is unavailable");
      }

      const [currentUser] = await db
        .select({ platformRole: users.platformRole })
        .from(users)
        .where(eq(users.id, userId))
        .limit(1);
      if (!currentUser) throw new Error("Session identity is no longer active");

      token.id = userId;
      token.platformRole = currentUser.platformRole ?? "contributor";
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
      const sessionUser = session.user as {
        id?: string;
        platformRole?: string;
      };
      sessionUser.id = token.id;
      sessionUser.platformRole = token.platformRole;
      return session;
    },
  },
  pages: {
    signIn: "/login",
  },
};
