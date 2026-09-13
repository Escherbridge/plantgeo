import { inArray } from "drizzle-orm";
import { z } from "zod";
import { protectedProcedure, router } from "@/lib/server/trpc/init";
import { users } from "@/lib/server/db/schema";

/**
 * The minimal user directory: user id -> display name and avatar, nothing else.
 *
 * Two rules hold this surface in place and both are pinned by
 * `src/__tests__/trpc/users.test.ts`:
 *
 *  - The projection is EXACTLY `{ id, name, image }`. `email`, `passwordHash`,
 *    `platformRole`, `verified` and `activeTeamId` are columns of the same
 *    table, so the read is an explicit column list, never `select()` over the
 *    whole row -- a directory that widened by accident would leak credentials
 *    and role state to every signed-in reader.
 *  - `name` comes back as null when unset. The id-fragment fallback
 *    ("Contributor 1a2b3c4d") lives in the client component that already owned
 *    it (`use-display-names.ts`), so there is one fallback string in the
 *    codebase rather than one per caller.
 *
 * Authentication, not authorization: any signed-in caller may resolve any id.
 * A display name is already visible to every reader of a comment thread, so a
 * per-id visibility rule here would gate nothing the caller cannot already see.
 */

/** The ceiling on one batch, matching this router set's `.max()` array convention. */
export const MAX_DISPLAY_NAME_IDS = 100;

export const usersRouter = router({
  /**
   * Resolve a batch of user ids to display names.
   *
   * Returns one row per id that exists, in no guaranteed order; an unknown id
   * is simply absent, which the client treats the same as a null name.
   */
  getDisplayNames: protectedProcedure
    .input(
      z.object({
        userIds: z.array(z.string().uuid()).min(1).max(MAX_DISPLAY_NAME_IDS),
      })
    )
    .query(async ({ ctx, input }) => {
      // De-duplicated before the query: a thread of forty comments by three
      // people is three ids on the wire, not forty.
      const wanted = Array.from(new Set(input.userIds));

      const rows = await ctx.db
        .select({
          id: users.id,
          name: users.name,
          image: users.image,
        })
        .from(users)
        .where(inArray(users.id, wanted));

      return rows;
    }),
});

export type DisplayNameRow = {
  id: string;
  name: string | null;
  image: string | null;
};
