import { and, eq, isNull } from "drizzle-orm";
import { affiliates, users } from "@/lib/server/db/schema";
import { normalizeAuthEmail } from "@/lib/server/security/registration";
import type { AppDatabase } from "@/lib/server/affiliate/database";

export type AffiliateRoleGrant = "granted" | "already" | "preserved" | "none";

/**
 * On sign-in, a matching active affiliate becomes role `affiliate` when the
 * account is still a contributor. Admin, expert, and viewer stay as they are:
 * approval must not remove a privileged role or turn a viewer into a contributor.
 */
export async function grantAffiliateRoleOnSignIn(
  database: AppDatabase,
  userId: string
): Promise<AffiliateRoleGrant> {
  const [user] = await database
    .select({ email: users.email, platformRole: users.platformRole })
    .from(users)
    .where(eq(users.id, userId))
    .limit(1);
  if (!user || typeof user.email !== "string") return "none";

  const email = normalizeAuthEmail(user.email);
  if (email.length === 0) return "none";

  const [affiliate] = await database
    .select({ id: affiliates.id })
    .from(affiliates)
    .where(and(eq(affiliates.email, email), eq(affiliates.status, "active")))
    .limit(1);
  if (!affiliate) return "none";

  await database
    .update(affiliates)
    .set({ userId })
    .where(and(eq(affiliates.id, affiliate.id), isNull(affiliates.userId)));

  const current = user.platformRole;
  if (current === "affiliate") return "already";
  if (current !== "contributor" && current != null) return "preserved";

  await database
    .update(users)
    .set({ platformRole: "affiliate" })
    .where(
      current == null
        ? and(eq(users.id, userId), isNull(users.platformRole))
        : and(eq(users.id, userId), eq(users.platformRole, "contributor"))
    );
  return "granted";
}
