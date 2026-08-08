/**
 * Sets one account's platform role.
 *
 * Why this exists: `users.platform_role` is written in exactly one place in the application
 * -- `src/app/api/auth/register/route.ts`, hardcoded to "contributor" -- and no tRPC
 * procedure, API route or admin screen ever changes it. That makes `expert` and `admin`
 * unreachable through the product, and with them `/moderation`, the Moderation nav item and
 * every `expertProcedure`, for every account that exists. This is the operator path onto
 * that ladder; the registration default is deliberately left alone.
 *
 * Usage:
 *   npx tsx scripts/promote-user.ts --email someone@example.com --role admin
 *
 * Reads PROMOTE_DATABASE_URL, else DATABASE_URL. Touches `platform_role` and nothing else.
 */
import { eq, sql } from "drizzle-orm";
import { drizzle } from "drizzle-orm/postgres-js";
import postgres from "postgres";
import { users } from "../src/lib/server/db/schema";

/**
 * The role vocabulary the application enforces, taken from the gates rather than from the
 * column: `users.platform_role` is `varchar(20)` with a "contributor" default and no
 * database enum (src/lib/server/db/schema.ts), so a typo would be stored happily and would
 * then fail every gate silently, locking the account out instead of erroring. These are the
 * exact strings `contributorProcedure` / `expertProcedure` / `adminProcedure` test in
 * src/lib/server/trpc/init.ts, which `MODERATION_ROLES` mirrors in
 * src/app/moderation/page.tsx and src/components/layout/TopBar.tsx.
 */
const PLATFORM_ROLES = ["contributor", "expert", "admin"] as const;

type PlatformRole = (typeof PLATFORM_ROLES)[number];

const USAGE =
  "usage: npx tsx scripts/promote-user.ts --email <address> --role <" +
  PLATFORM_ROLES.join("|") +
  ">";

/** Reads `--name value` or `--name=value` out of argv. */
function readFlag(name: string): string | null {
  const flag = `--${name}`;
  const index = process.argv.indexOf(flag);
  if (index !== -1) {
    const next: string | undefined = process.argv[index + 1];
    return next ?? null;
  }
  const inline = process.argv.find((argument) => argument.startsWith(`${flag}=`));
  return inline === undefined ? null : inline.slice(flag.length + 1);
}

function isPlatformRole(value: string): value is PlatformRole {
  return (PLATFORM_ROLES as readonly string[]).includes(value);
}

/** A bad invocation or an unknown account: reported with the usage line, nothing written. */
class UsageError extends Error {}

/**
 * The half of this operation that is not a database write. Both readers of the role --
 * src/app/moderation/page.tsx and the Moderation nav item in
 * src/components/layout/TopBar.tsx -- read the NextAuth session, never this row, so the
 * promotion is invisible until the session token is reissued and an operator who skips this
 * step will reasonably conclude the script did nothing.
 *
 * Verified nuance: the jwt callback in src/lib/server/auth-options.ts DOES re-read the user
 * row (through loadOrganizationContext) on every token refresh, not only at sign-in, so a
 * full page load normally picks the new role up. A tab already holding a decoded session
 * will not.
 */
const SESSION_REMINT_NOTICE = [
  "",
  "promote-user: WARNING - the session must be reissued before this takes effect.",
  "promote-user:   /moderation and the Moderation nav item read the NextAuth session, not",
  "promote-user:   this row. auth-options.ts re-reads the user on every token refresh, so a",
  "promote-user:   full page load usually suffices; an already-open tab will keep showing the",
  "promote-user:   old role. Sign out and back in for certainty.",
].join("\n");

async function promoteUser(): Promise<void> {
  const email = (readFlag("email") ?? "").trim();
  const role = (readFlag("role") ?? "").trim();

  if (email === "") throw new UsageError("--email is required");
  if (!isPlatformRole(role)) {
    throw new UsageError(
      `--role must be one of ${PLATFORM_ROLES.join(", ")}; got ${JSON.stringify(role)}`
    );
  }

  const connectionString =
    process.env.PROMOTE_DATABASE_URL || process.env.DATABASE_URL;
  if (!connectionString) {
    throw new UsageError("neither PROMOTE_DATABASE_URL nor DATABASE_URL is set");
  }

  // Mirrors scripts/migrate.mjs: one session, no forced TLS, notices silenced.
  const client = postgres(connectionString, {
    max: 1,
    idle_timeout: 5,
    connect_timeout: 30,
    onnotice: () => {},
  });
  const database = drizzle(client);

  try {
    // Case-insensitive because registration stores the lowercased address
    // (normalizeAuthEmail in src/lib/server/security/registration.ts) while an OAuth
    // profile can arrive in any casing -- an exact match would report "no account" for a
    // user who plainly exists. The update below keys on the id this resolves to.
    const [before] = await database
      .select({
        id: users.id,
        email: users.email,
        name: users.name,
        platformRole: users.platformRole,
      })
      .from(users)
      .where(sql`lower(${users.email}) = lower(${email})`)
      .limit(1);

    if (!before) {
      throw new UsageError(`no account found for ${email}`);
    }

    console.log(`promote-user: ${before.email} (${before.name ?? "no name"})`);
    console.log(`promote-user:   before  platform_role = ${before.platformRole ?? "null"}`);

    if (before.platformRole === role) {
      console.log(`promote-user:   after   platform_role = ${role} (already set, no write)`);
      // Printed here too: "I already ran this and nothing changed" is exactly the symptom
      // of a session that has not been reissued.
      console.log(SESSION_REMINT_NOTICE);
      return;
    }

    const [after] = await database
      .update(users)
      .set({ platformRole: role })
      .where(eq(users.id, before.id))
      .returning({ platformRole: users.platformRole });

    console.log(`promote-user:   after   platform_role = ${after?.platformRole ?? "null"}`);
    console.log(SESSION_REMINT_NOTICE);
  } finally {
    await client.end({ timeout: 5 }).catch(() => {});
  }
}

// tsx transpiles this repo's scripts to CJS (no "type": "module" in package.json),
// which forbids top-level await — so the entry point is a promise chain, not awaited.
promoteUser().catch((error: unknown) => {
  if (error instanceof UsageError) {
    console.error(`promote-user: ${error.message}`);
    console.error(USAGE);
  } else {
    console.error("promote-user: failed");
    console.error(error);
  }
  process.exitCode = 1;
});
