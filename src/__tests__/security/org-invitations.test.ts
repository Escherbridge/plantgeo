import { beforeEach, describe, expect, it, vi } from "vitest";
import { PgDialect } from "drizzle-orm/pg-core";
import {
  canCreateJoinLink,
  canInviteTeamRole,
  canManageInvitations,
  canRevokeJoinLink,
  resolveDelegableRole,
  resolveTeamRole,
} from "@/lib/server/security/access-control";
import {
  canRedeemJoinLink,
  emailDomain,
  emailMatchesInvitation,
  EMAIL_VERIFICATION_REQUIRED_MESSAGE,
  invitationState,
  isInvitationRedeemable,
  joinLinkRejectionMessage,
  joinLinkState,
  normalizeEmail,
  normalizeEmailDomain,
  remainingJoinLinkUses,
} from "@/lib/server/security/invitations";
import {
  generateUniqueSlug,
  isReservedSlug,
  normalizeSlug,
  slugify,
} from "@/lib/server/security/org-slug";

vi.mock("@/lib/server/db", () => ({ db: {} }));
vi.mock("@/lib/server/auth", () => ({ getServerSession: vi.fn() }));
vi.mock("@/lib/server/middleware/api-auth", () => ({
  checkRateLimit: vi.fn(async () => ({ limited: false, available: true })),
}));
vi.mock("@/lib/server/security/tokens", () => ({
  generateToken: () => ({ token: "raw-token-value", tokenHash: "hashed" }),
  hashToken: (token: string) => `hashed:${token}`,
  tokenExpiry: (ttl: number) => new Date(Date.now() + ttl),
  timingSafeTokenEqual: (a: string, b: string) => a === b,
  TOKEN_TTL: {
    emailVerification: 86_400_000,
    passwordReset: 3_600_000,
    invitation: 604_800_000,
  },
}));
vi.mock("@/lib/server/services/transactional-email", () => ({
  sendOrgInvitation: vi.fn(async () => undefined),
  appUrl: () => "https://plantgeo.test",
}));

import { checkRateLimit } from "@/lib/server/middleware/api-auth";
import type { Context } from "@/lib/server/trpc/init";
import * as trpcInit from "@/lib/server/trpc/init";
import {
  joinLinkClaimCondition,
  teamsRouter,
} from "@/lib/server/trpc/routers/teams";

const TEAM_ID = "11111111-1111-4111-8111-111111111111";
const USER_ID = "22222222-2222-4222-8222-222222222222";
const OTHER_ID = "33333333-3333-4333-8333-333333333333";
const LINK_ID = "44444444-4444-4444-8444-444444444444";
const VALID_TOKEN = "abcdefghijklmnopqrstuvwxyz012345";

type Row = Record<string, unknown>;

/** Every builder call the router made, so writes can be asserted on. */
type BuilderCall = { method: string; args: unknown[] };

/**
 * Chainable drizzle stand-in: every builder method records its arguments and
 * returns itself, and awaiting any terminal shifts the next scripted result set
 * off the queue.
 */
function createQueryStub(queue: Row[][], calls: BuilderCall[]): unknown {
  const proxy: unknown = new Proxy(() => {}, {
    get(_target, property) {
      if (property === "then") {
        return (resolve: (rows: Row[]) => unknown) =>
          resolve(queue.shift() ?? []);
      }
      return (...args: unknown[]) => {
        calls.push({ method: String(property), args });
        return proxy;
      };
    },
  });
  return proxy;
}

function createScriptedDatabase(
  script: Row[][],
  calls: BuilderCall[] = []
): Context["db"] {
  const queue = script.map((rows) => [...rows]);
  const runner = {
    select: () => createQueryStub(queue, calls),
    insert: () => createQueryStub(queue, calls),
    update: () => createQueryStub(queue, calls),
    delete: () => createQueryStub(queue, calls),
    execute: async () => [],
    transaction: async (callback: (tx: unknown) => Promise<unknown>) =>
      callback(runner),
  };
  return runner as unknown as Context["db"];
}

/** The payload of the first `.values(...)` the router wrote. */
function insertedValues(calls: BuilderCall[]): Row {
  const call = calls.find((entry) => entry.method === "values");
  return (call?.args[0] ?? {}) as Row;
}

function sessionFor(
  overrides: { id?: string; email?: string | null; name?: string | null } = {}
): NonNullable<Context["session"]> {
  return {
    expires: "2099-01-01T00:00:00.000Z",
    user: {
      id: overrides.id ?? USER_ID,
      email: overrides.email === undefined ? "invitee@example.com" : overrides.email,
      name: overrides.name ?? "Invitee",
    },
  } as NonNullable<Context["session"]>;
}

function callerWith(
  script: Row[][],
  session = sessionFor(),
  calls: BuilderCall[] = []
) {
  return teamsRouter.createCaller({
    db: createScriptedDatabase(script, calls),
    session,
  });
}

const future = new Date(Date.now() + 86_400_000);
const past = new Date(Date.now() - 86_400_000);

/**
 * The `users` row every redemption re-reads inside its transaction. The session
 * claim is deliberately irrelevant: only this row authorizes.
 */
function accountRow(
  overrides: { email?: string | null; emailVerified?: Date | null } = {}
): Row[] {
  return [
    {
      email: overrides.email === undefined ? "invitee@example.com" : overrides.email,
      emailVerified:
        overrides.emailVerified === undefined ? past : overrides.emailVerified,
    },
  ];
}

const ORG_ROW: Row[] = [{ id: TEAM_ID, name: "Acme", slug: "acme" }];
const NO_MEMBERSHIP: Row[] = [];

function invitationRow(overrides: Row = {}): Row[] {
  return [
    {
      id: "inv-1",
      teamId: TEAM_ID,
      email: "invitee@example.com",
      teamRole: "member",
      tokenHash: `hashed:${VALID_TOKEN}`,
      expiresAt: future,
      acceptedAt: null,
      acceptedBy: null,
      revokedAt: null,
      ...overrides,
    },
  ];
}

function joinLinkRow(overrides: Row = {}): Row[] {
  return [
    {
      id: LINK_ID,
      teamId: TEAM_ID,
      codeHash: `hashed:${VALID_TOKEN}`,
      teamRole: "viewer",
      allowedEmailDomain: null,
      maxUses: null,
      useCount: 0,
      expiresAt: null,
      revokedAt: null,
      ...overrides,
    },
  ];
}

describe("organization role delegation", () => {
  it("never lets an invitation or join link grant ownership", () => {
    expect(canInviteTeamRole("owner", "owner")).toBe(false);
    expect(canInviteTeamRole("member", "owner")).toBe(false);
    expect(canInviteTeamRole(null, "member")).toBe(false);
    expect(canInviteTeamRole(undefined, "viewer")).toBe(false);
    expect(canInviteTeamRole("viewer", "viewer")).toBe(false);
    expect(canInviteTeamRole("owner", "member")).toBe(true);
  });

  it("scopes invitation and join-link management by role", () => {
    expect(canManageInvitations("owner")).toBe(true);
    expect(canManageInvitations("member")).toBe(true);
    expect(canManageInvitations("viewer")).toBe(false);
    expect(canManageInvitations(null)).toBe(false);
    expect(canCreateJoinLink("owner")).toBe(true);
    expect(canCreateJoinLink("member")).toBe(false);
    expect(canRevokeJoinLink("owner")).toBe(true);
    expect(canRevokeJoinLink("member")).toBe(false);
  });

  it("narrows only known team roles", () => {
    expect(resolveTeamRole("owner")).toBe("owner");
    expect(resolveTeamRole("superuser")).toBeNull();
    expect(resolveTeamRole(null)).toBeNull();
    expect(resolveTeamRole(42)).toBeNull();
  });

  it("clamps every non-delegable role down to viewer", () => {
    expect(resolveDelegableRole("member")).toBe("member");
    expect(resolveDelegableRole("viewer")).toBe("viewer");
    expect(resolveDelegableRole("owner")).toBe("viewer");
    expect(resolveDelegableRole("superuser")).toBe("viewer");
    expect(resolveDelegableRole(null)).toBe("viewer");
    expect(resolveDelegableRole(undefined)).toBe("viewer");
  });
});

describe("trpc procedure surface", () => {
  it("exposes no organization-scoped procedure to authorize on a session role", () => {
    expect(Object.keys(trpcInit)).not.toContain("orgProcedure");
    expect(Object.keys(trpcInit)).toEqual(
      expect.arrayContaining([
        "router",
        "publicProcedure",
        "protectedProcedure",
        "contributorProcedure",
        "expertProcedure",
        "adminProcedure",
      ])
    );
  });
});

describe("invitation state machine", () => {
  it("treats terminal states as terminal", () => {
    expect(invitationState({ expiresAt: future })).toBe("pending");
    expect(invitationState({ expiresAt: past })).toBe("expired");
    expect(invitationState({ expiresAt: future, revokedAt: past })).toBe("revoked");
    expect(invitationState({ expiresAt: past, acceptedAt: past })).toBe("accepted");
    expect(invitationState({ expiresAt: null })).toBe("expired");
    expect(isInvitationRedeemable({ expiresAt: future })).toBe(true);
    expect(isInvitationRedeemable({ expiresAt: future, revokedAt: past })).toBe(
      false
    );
  });

  it("matches invited addresses case-insensitively", () => {
    expect(normalizeEmail("  USER@Example.COM ")).toBe("user@example.com");
    expect(emailMatchesInvitation("User@Example.com", "user@example.COM")).toBe(
      true
    );
    expect(emailMatchesInvitation("user@example.com", "other@example.com")).toBe(
      false
    );
    expect(emailMatchesInvitation("user@example.com", null)).toBe(false);
    expect(emailMatchesInvitation(null, "user@example.com")).toBe(false);
  });
});

describe("join-link redemption policy", () => {
  it("honours revocation, expiry and the use budget", () => {
    expect(canRedeemJoinLink({ expiresAt: future })).toEqual({ allowed: true });
    expect(canRedeemJoinLink({ revokedAt: past })).toEqual({
      allowed: false,
      reason: "revoked",
    });
    expect(canRedeemJoinLink({ expiresAt: past })).toEqual({
      allowed: false,
      reason: "expired",
    });
    expect(canRedeemJoinLink({ maxUses: 2, useCount: 2 })).toEqual({
      allowed: false,
      reason: "exhausted",
    });
    expect(canRedeemJoinLink({ maxUses: 2, useCount: 1 })).toEqual({
      allowed: true,
    });
    expect(remainingJoinLinkUses({ maxUses: 3, useCount: 1 })).toBe(2);
    expect(remainingJoinLinkUses({ maxUses: null })).toBeNull();
    expect(joinLinkState({ maxUses: 1, useCount: 1 })).toBe("exhausted");
  });

  it("enforces the email-domain allowlist", () => {
    const link = { allowedEmailDomain: "@ACME.com" };
    const verified = (email: string) => ({ email, emailVerified: true });
    expect(canRedeemJoinLink(link, new Date(), verified("person@acme.com"))).toEqual({
      allowed: true,
    });
    expect(canRedeemJoinLink(link, new Date(), verified("person@evil.com"))).toEqual({
      allowed: false,
      reason: "domain_mismatch",
    });
    expect(canRedeemJoinLink(link, new Date(), null)).toEqual({
      allowed: false,
      reason: "missing_email",
    });
    expect(
      canRedeemJoinLink(link, new Date(), { email: null, emailVerified: true })
    ).toEqual({ allowed: false, reason: "missing_email" });
    expect(
      canRedeemJoinLink(link, new Date(), verified("person@sub.acme.com"))
    ).toEqual({ allowed: false, reason: "domain_mismatch" });
    expect(emailDomain("Person@Acme.com")).toBe("acme.com");
    expect(emailDomain("not-an-email")).toBeNull();
    expect(normalizeEmailDomain("not a domain")).toBeNull();
  });

  it("treats an unverified address as no address at all under a domain rule", () => {
    const unverified = { email: "attacker@acme.com", emailVerified: false };
    expect(
      canRedeemJoinLink({ allowedEmailDomain: "acme.com" }, new Date(), unverified)
    ).toEqual({ allowed: false, reason: "unverified_email" });
    // Without a domain rule the address authorizes nothing, so it is not gated.
    expect(canRedeemJoinLink({ maxUses: null }, new Date(), unverified)).toEqual({
      allowed: true,
    });
    expect(joinLinkRejectionMessage("unverified_email")).toBe(
      EMAIL_VERIFICATION_REQUIRED_MESSAGE
    );
    // The old "a verified email address is required" copy was never enforced.
    expect(joinLinkRejectionMessage("missing_email")).not.toContain("verified");
  });

  it("re-checks the use budget inside the claiming UPDATE", () => {
    const query = new PgDialect().sqlToQuery(
      joinLinkClaimCondition(LINK_ID, new Date())
    );
    expect(query.sql).toContain('"team_join_links"."revoked_at" is null');
    expect(query.sql).toContain(
      '"team_join_links"."use_count" < "team_join_links"."max_uses"'
    );
    expect(query.sql).toContain('"team_join_links"."expires_at" >');
    expect(query.params).toContain(LINK_ID);
  });
});

describe("organization slugs", () => {
  it("normalizes names into safe slugs", () => {
    expect(slugify("  Acme   Forestry!!  ")).toBe("acme-forestry");
    expect(slugify("--Trailing--")).toBe("trailing");
    expect(slugify("!!!")).toBe("");
    expect(slugify("A".repeat(200)).length).toBeLessThanOrEqual(100);
  });

  it("refuses reserved and malformed slugs", () => {
    expect(isReservedSlug("Admin")).toBe(true);
    expect(isReservedSlug("acme")).toBe(false);
    expect(normalizeSlug("Dashboard")).toBeNull();
    expect(normalizeSlug("!!!")).toBeNull();
    expect(normalizeSlug(" Acme Forestry ")).toBe("acme-forestry");
  });

  it("suffixes a colliding slug instead of failing the insert", async () => {
    const runner = createScriptedDatabase([[{ id: "taken" }], []]);
    const slug = await generateUniqueSlug(runner, "Acme Forestry");
    expect(slug).not.toBe("acme-forestry");
    expect(slug.startsWith("acme-forestry-")).toBe(true);
    expect(slug).toMatch(/^[a-z0-9]+(?:-[a-z0-9]+)*$/);
  });
});

describe("teams router invitation flows", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("exposes the frozen procedure surface", () => {
    const procedures = Object.keys(teamsRouter._def.procedures);
    for (const name of [
      "listMyTeams",
      "createTeam",
      "updateTeam",
      "listMembers",
      "removeMember",
      "updateMemberRole",
      "leaveTeam",
      "setActiveTeam",
      "createInvitation",
      "listInvitations",
      "revokeInvitation",
      "previewInvitation",
      "acceptInvitation",
      "createJoinLink",
      "listJoinLinks",
      "rotateJoinLink",
      "revokeJoinLink",
      "previewJoinLink",
      "redeemJoinLink",
      "getTeamProfile",
      "getTeamDashboard",
      "getTeamsInBbox",
      "verifyTeam",
    ]) {
      expect(procedures).toContain(name);
    }
  });

  it("rejects an invitation that would delegate ownership", async () => {
    const caller = callerWith([]);
    await expect(
      caller.createInvitation({
        teamId: TEAM_ID,
        email: "new@example.com",
        teamRole: "owner" as unknown as "member",
      })
    ).rejects.toMatchObject({ code: "BAD_REQUEST" });
  });

  it("refuses invitations from a non-member without spending rate-limit quota", async () => {
    const caller = callerWith([[]]);
    await expect(
      caller.createInvitation({
        teamId: TEAM_ID,
        email: "new@example.com",
        teamRole: "member",
      })
    ).rejects.toMatchObject({ code: "FORBIDDEN" });
    expect(checkRateLimit).not.toHaveBeenCalled();
  });

  it("refuses to let a member replace an invitation it could not have issued", async () => {
    // A member may invite viewers, so rewriting an owner's pending `member`
    // invite down to `viewer` would silently kill the emailed link.
    const caller = callerWith([
      [{ teamRole: "member" }],
      ORG_ROW,
      [],
      [{ id: "inv-1", teamRole: "member" }],
    ]);
    await expect(
      caller.createInvitation({
        teamId: TEAM_ID,
        email: "invitee@example.com",
        teamRole: "viewer",
      })
    ).rejects.toMatchObject({ code: "CONFLICT" });
  });

  it("refuses to replace a pending invitation carrying a non-delegable role", async () => {
    const caller = callerWith([
      [{ teamRole: "owner" }],
      ORG_ROW,
      [],
      [{ id: "inv-1", teamRole: "owner" }],
    ]);
    await expect(
      caller.createInvitation({
        teamId: TEAM_ID,
        email: "invitee@example.com",
        teamRole: "member",
      })
    ).rejects.toMatchObject({ code: "CONFLICT" });
  });

  it("still lets an owner re-issue a pending member invitation", async () => {
    const caller = callerWith([
      [{ teamRole: "owner" }],
      ORG_ROW,
      [],
      [{ id: "inv-1", teamRole: "member" }],
      [
        {
          id: "inv-1",
          email: "invitee@example.com",
          teamRole: "viewer",
          expiresAt: future,
        },
      ],
    ]);
    await expect(
      caller.createInvitation({
        teamId: TEAM_ID,
        email: "invitee@example.com",
        teamRole: "viewer",
      })
    ).resolves.toMatchObject({ id: "inv-1", teamRole: "viewer" });
  });

  it("still lets a member re-send its own viewer invitation", async () => {
    const caller = callerWith([
      [{ teamRole: "member" }],
      ORG_ROW,
      [],
      [{ id: "inv-1", teamRole: "viewer" }],
      [
        {
          id: "inv-1",
          email: "invitee@example.com",
          teamRole: "viewer",
          expiresAt: future,
        },
      ],
    ]);
    await expect(
      caller.createInvitation({
        teamId: TEAM_ID,
        email: "invitee@example.com",
        teamRole: "viewer",
      })
    ).resolves.toMatchObject({ id: "inv-1", teamRole: "viewer" });
  });

  it("returns the live accept link only when the caller asks for it", async () => {
    const script = (): Row[][] => [
      [{ teamRole: "owner" }],
      ORG_ROW,
      [],
      [],
      [
        {
          id: "inv-1",
          email: "new@example.com",
          teamRole: "member",
          expiresAt: future,
        },
      ],
    ];

    const withLink = await callerWith(script()).createInvitation({
      teamId: TEAM_ID,
      email: "new@example.com",
      teamRole: "member",
      returnLink: true,
    });
    expect(withLink.acceptUrl).toBe(
      "https://plantgeo.test/api/invitations/raw-token-value"
    );

    const withoutLink = await callerWith(script()).createInvitation({
      teamId: TEAM_ID,
      email: "new@example.com",
      teamRole: "member",
      returnLink: false,
    });
    expect(withoutLink.acceptUrl).toBe("");
  });

  it("authorizes the organization before resolving the invited user", async () => {
    // Resolving first let an outsider tell "real user" from "not your org".
    const caller = callerWith([[]]);
    await expect(
      caller.inviteMember({
        teamId: TEAM_ID,
        userId: OTHER_ID,
        teamRole: "viewer",
      })
    ).rejects.toMatchObject({ code: "FORBIDDEN" });
  });

  it("requires the stored email to match the invited address", async () => {
    const caller = callerWith([
      invitationRow({ email: "someone-else@example.com" }),
      accountRow(),
    ]);
    await expect(
      caller.acceptInvitation({ token: VALID_TOKEN })
    ).rejects.toMatchObject({ code: "FORBIDDEN" });
  });

  it("authorizes on the stored email, not on the session's stale claim", async () => {
    // The JWT claim is minted at sign-in and never refreshed, so it is ignored:
    // a stale claim must neither grant nor deny acceptance.
    const staleSession = sessionFor({ email: "someone-else@example.com" });
    const accepted = await callerWith(
      [
        invitationRow({}),
        accountRow(),
        ORG_ROW,
        NO_MEMBERSHIP,
        [{ id: "inv-1" }],
        [],
        [],
      ],
      staleSession
    ).acceptInvitation({ token: VALID_TOKEN });
    expect(accepted).toMatchObject({
      teamId: TEAM_ID,
      teamRole: "member",
      alreadyMember: false,
    });

    // ...and the converse: a session claiming the invited address cannot stand
    // in for a database row that says otherwise.
    await expect(
      callerWith([
        invitationRow({ email: "someone-else@example.com" }),
        accountRow({ email: "invitee@example.com" }),
      ]).acceptInvitation({ token: VALID_TOKEN })
    ).rejects.toMatchObject({ code: "FORBIDDEN" });
  });

  it("refuses acceptance from an account with an unverified email", async () => {
    const caller = callerWith([
      invitationRow({}),
      accountRow({ emailVerified: null }),
    ]);
    await expect(
      caller.acceptInvitation({ token: VALID_TOKEN })
    ).rejects.toMatchObject({
      code: "FORBIDDEN",
      message: EMAIL_VERIFICATION_REQUIRED_MESSAGE,
    });
  });

  it("refuses acceptance from an account with no email at all", async () => {
    const caller = callerWith([
      invitationRow({}),
      accountRow({ email: null }),
    ]);
    await expect(
      caller.acceptInvitation({ token: VALID_TOKEN })
    ).rejects.toMatchObject({ code: "FORBIDDEN" });
  });

  it("clamps an invitation row smuggling `owner` down to viewer", async () => {
    const calls: BuilderCall[] = [];
    const accepted = await callerWith(
      [
        invitationRow({ teamRole: "owner" }),
        accountRow(),
        ORG_ROW,
        NO_MEMBERSHIP,
        [{ id: "inv-1" }],
        [],
        [],
      ],
      sessionFor(),
      calls
    ).acceptInvitation({ token: VALID_TOKEN });
    expect(accepted.teamRole).toBe("viewer");
    expect(insertedValues(calls)).toMatchObject({
      teamId: TEAM_ID,
      userId: USER_ID,
      teamRole: "viewer",
    });
  });

  it("cannot replay an invitation that was already accepted", async () => {
    const caller = callerWith([
      invitationRow({ acceptedAt: past, acceptedBy: OTHER_ID }),
      accountRow(),
      ORG_ROW,
      NO_MEMBERSHIP,
    ]);
    await expect(
      caller.acceptInvitation({ token: VALID_TOKEN })
    ).rejects.toMatchObject({ code: "PRECONDITION_FAILED" });
  });

  it("rejects expired and revoked invitations", async () => {
    const script = (overrides: Row) => [
      invitationRow(overrides),
      accountRow(),
      ORG_ROW,
      NO_MEMBERSHIP,
    ];

    await expect(
      callerWith(script({ expiresAt: past })).acceptInvitation({
        token: VALID_TOKEN,
      })
    ).rejects.toMatchObject({ code: "PRECONDITION_FAILED" });

    await expect(
      callerWith(script({ revokedAt: past })).acceptInvitation({
        token: VALID_TOKEN,
      })
    ).rejects.toMatchObject({ code: "PRECONDITION_FAILED" });
  });

  it("rejects an unknown invitation token without leaking existence detail", async () => {
    const caller = callerWith([[]]);
    await expect(
      caller.acceptInvitation({ token: VALID_TOKEN })
    ).rejects.toMatchObject({ code: "NOT_FOUND" });
  });

  it("blocks a join link redeemed from the wrong email domain", async () => {
    const caller = callerWith([
      joinLinkRow({ allowedEmailDomain: "acme.com" }),
      accountRow(),
      ORG_ROW,
      NO_MEMBERSHIP,
    ]);
    await expect(
      caller.redeemJoinLink({ code: VALID_TOKEN })
    ).rejects.toMatchObject({ code: "FORBIDDEN" });
  });

  it("blocks a domain-restricted link when the address is unverified", async () => {
    // Registering attacker@acme.com is free; proving control of it is not.
    const caller = callerWith([
      joinLinkRow({ allowedEmailDomain: "acme.com" }),
      accountRow({ email: "attacker@acme.com", emailVerified: null }),
      ORG_ROW,
      NO_MEMBERSHIP,
    ]);
    await expect(
      caller.redeemJoinLink({ code: VALID_TOKEN })
    ).rejects.toMatchObject({
      code: "FORBIDDEN",
      message: EMAIL_VERIFICATION_REQUIRED_MESSAGE,
    });
  });

  it("still admits an unverified address to an unrestricted link", async () => {
    // Documented decision: with no domain rule the address authorizes nothing,
    // so verification would only add friction (see canRedeemJoinLink).
    const joined = await callerWith([
      joinLinkRow({}),
      accountRow({ emailVerified: null }),
      ORG_ROW,
      NO_MEMBERSHIP,
      [{ useCount: 1 }],
      [],
      [],
    ]).redeemJoinLink({ code: VALID_TOKEN });
    expect(joined).toMatchObject({ teamRole: "viewer", alreadyMember: false });
  });

  it("clamps a join-link row smuggling `owner` down to viewer", async () => {
    const calls: BuilderCall[] = [];
    const joined = await callerWith(
      [
        joinLinkRow({ teamRole: "owner" }),
        accountRow(),
        ORG_ROW,
        NO_MEMBERSHIP,
        [{ useCount: 1 }],
        [],
        [],
      ],
      sessionFor(),
      calls
    ).redeemJoinLink({ code: VALID_TOKEN });
    expect(joined.teamRole).toBe("viewer");
    expect(insertedValues(calls)).toMatchObject({
      teamId: TEAM_ID,
      userId: USER_ID,
      teamRole: "viewer",
    });
  });

  it("blocks a join link whose use budget is spent", async () => {
    const caller = callerWith([
      joinLinkRow({ maxUses: 1, useCount: 1 }),
      accountRow(),
      ORG_ROW,
      NO_MEMBERSHIP,
    ]);
    await expect(
      caller.redeemJoinLink({ code: VALID_TOKEN })
    ).rejects.toMatchObject({ code: "PRECONDITION_FAILED" });
  });

  it("re-validates the copied role when a join link is rotated", async () => {
    const calls: BuilderCall[] = [];
    await callerWith(
      [
        [
          {
            id: LINK_ID,
            teamId: TEAM_ID,
            teamRole: "owner",
            allowedEmailDomain: null,
            maxUses: null,
            expiresAt: null,
          },
        ],
        [{ teamRole: "owner" }],
        [],
        [
          {
            id: "link-2",
            teamRole: "viewer",
            allowedEmailDomain: null,
            maxUses: null,
            expiresAt: null,
          },
        ],
      ],
      sessionFor(),
      calls
    ).rotateJoinLink({ linkId: LINK_ID });
    expect(insertedValues(calls)).toMatchObject({ teamRole: "viewer" });
  });

  it("refuses to let the last owner leave", async () => {
    const caller = callerWith([[{ teamRole: "owner" }], [{ count: 1 }]]);
    await expect(caller.leaveTeam({ teamId: TEAM_ID })).rejects.toMatchObject({
      code: "CONFLICT",
    });
  });

  it("lets an owner leave when another owner remains", async () => {
    const caller = callerWith([[{ teamRole: "owner" }], [{ count: 2 }], [], []]);
    await expect(caller.leaveTeam({ teamId: TEAM_ID })).resolves.toEqual({
      success: true,
    });
  });
});

describe("public preview procedures", () => {
  it("returns only name, invited email and role for an invitation", async () => {
    const caller = callerWith([
      [
        {
          tokenHash: `hashed:${VALID_TOKEN}`,
          email: "invitee@example.com",
          teamRole: "member",
          expiresAt: future,
          acceptedAt: null,
          revokedAt: null,
          orgName: "Acme",
        },
      ],
    ]);
    const preview = await caller.previewInvitation({ token: VALID_TOKEN });
    expect(Object.keys(preview).sort()).toEqual([
      "email",
      "orgName",
      "role",
      "valid",
    ]);
    expect(preview).toEqual({
      valid: true,
      orgName: "Acme",
      email: "invitee@example.com",
      role: "member",
    });
  });

  it("reveals nothing about the organization for a spent invitation", async () => {
    const caller = callerWith([
      [
        {
          tokenHash: `hashed:${VALID_TOKEN}`,
          email: "invitee@example.com",
          teamRole: "member",
          expiresAt: past,
          acceptedAt: null,
          revokedAt: null,
          orgName: "Acme",
        },
      ],
    ]);
    await expect(
      caller.previewInvitation({ token: VALID_TOKEN })
    ).resolves.toEqual({ valid: false });
  });

  it("returns only name and role for a join link, and nothing when revoked", async () => {
    const row = (overrides: Row) => [
      [
        {
          codeHash: `hashed:${VALID_TOKEN}`,
          teamRole: "viewer",
          maxUses: null,
          useCount: 0,
          expiresAt: null,
          revokedAt: null,
          orgName: "Acme",
          ...overrides,
        },
      ],
    ];

    const valid = await callerWith(row({})).previewJoinLink({
      code: VALID_TOKEN,
    });
    expect(Object.keys(valid).sort()).toEqual(["orgName", "role", "valid"]);
    expect(valid).toEqual({ valid: true, orgName: "Acme", role: "viewer" });

    await expect(
      callerWith(row({ revokedAt: past })).previewJoinLink({ code: VALID_TOKEN })
    ).resolves.toEqual({ valid: false });
  });

  it("never projects a token or code hash from the listing procedures", async () => {
    const caller = callerWith([
      [{ teamRole: "owner" }],
      [
        {
          id: "inv-1",
          email: "invitee@example.com",
          teamRole: "member",
          invitedBy: USER_ID,
          expiresAt: future,
          acceptedAt: null,
          revokedAt: null,
          createdAt: past,
        },
      ],
    ]);
    const invitations = await caller.listInvitations({ teamId: TEAM_ID });
    expect(invitations).toHaveLength(1);
    expect(Object.keys(invitations[0])).not.toContain("tokenHash");
    expect(JSON.stringify(invitations)).not.toContain("hashed");
  });
});
