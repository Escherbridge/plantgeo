import { router } from "@/lib/server/trpc/init";
import { organizationProcedures } from "./teams/organizations";
import { membershipProcedures } from "./teams/membership";
import { invitationProcedures } from "./teams/invitations";
import { joinLinkProcedures } from "./teams/join-links";
import { directoryProcedures } from "./teams/directory";

// "Organizations" are the teams/team_members tables. Invariants enforced here:
// membership, role and the caller's email address are always re-read from the
// database inside the acting transaction (never trusted from the JWT, which is
// minted at sign-in and never refreshed), only token/code hashes are stored,
// `owner` is never delegable through an invitation or join link, and every
// redemption is single-use and idempotent. The state machine itself lives in
// `@/lib/server/security/invitations`.
//
// Split by procedure group into `./teams/*` (organizations, membership,
// invitations, join-links, directory); this file only composes the public
// router shape. See `src/lib/server/AGENTS.md` §trpc / db / auth.

export const teamsRouter = router({
  ...organizationProcedures,
  ...membershipProcedures,
  ...invitationProcedures,
  ...joinLinkProcedures,
  ...directoryProcedures,
});

export { joinLinkClaimCondition } from "./teams/join-links";
