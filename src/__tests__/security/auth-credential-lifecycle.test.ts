import { createHash } from "crypto";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

/**
 * Drizzle's fluent builders are replaced with a recording stub: every chain
 * method returns the same thenable, and awaiting it yields the rows queued for
 * the table named by `.from(...)`. Writes are captured for assertions.
 */
const mocks = vi.hoisted(() => {
  interface Recorded {
    table: string;
    values: Record<string, unknown>;
    conditions: unknown[];
  }

  const state = {
    selectRows: {} as Record<string, unknown[]>,
    selectConditions: [] as unknown[],
    inserts: [] as Recorded[],
    updates: [] as Recorded[],
    returning: [] as unknown[],
    // Token burns gate on the affected-row count of `UPDATE ... RETURNING`,
    // so the stub has to model a write that matched at least one row.
    updateReturning: [{ id: "row" }] as unknown[],
    rateLimit: { available: true, limited: false } as unknown,
  };

  function fakeTable(name: string, columns: string[]) {
    const table: Record<string, unknown> = { __table: name };
    for (const column of columns) table[column] = `${name}.${column}`;
    return table;
  }

  const schema = {
    // The NextAuth adapter tables are only re-exported by auth-schema; a mocked
    // module must still provide every named export its importers reference.
    accounts: fakeTable("accounts", ["userId", "provider"]),
    sessions: fakeTable("sessions", ["sessionToken", "userId"]),
    verificationTokens: fakeTable("verification_tokens", ["identifier", "token"]),
    users: fakeTable("users", [
      "id",
      "name",
      "email",
      "emailVerified",
      "passwordHash",
      "platformRole",
      "activeTeamId",
    ]),
    teamMembers: fakeTable("team_members", [
      "teamId",
      "userId",
      "teamRole",
      "joinedAt",
    ]),
    emailVerificationTokens: fakeTable("email_verification_tokens", [
      "id",
      "userId",
      "tokenHash",
      "expiresAt",
      "usedAt",
    ]),
    passwordResetTokens: fakeTable("password_reset_tokens", [
      "id",
      "userId",
      "tokenHash",
      "expiresAt",
      "usedAt",
    ]),
  };

  function tableName(value: unknown): string {
    return (value as { __table?: string } | null)?.__table ?? "";
  }

  function selectBuilder() {
    let table = "";
    const builder: Record<string, unknown> = {
      from(source: unknown) {
        table = tableName(source);
        return builder;
      },
      leftJoin: () => builder,
      innerJoin: () => builder,
      where(condition: unknown) {
        state.selectConditions.push(condition);
        return builder;
      },
      orderBy: () => builder,
      limit: () => builder,
      for: () => builder,
      then(
        resolve: (rows: unknown[]) => unknown,
        reject?: (reason: unknown) => unknown
      ) {
        return Promise.resolve(state.selectRows[table] ?? []).then(resolve, reject);
      },
    };
    return builder;
  }

  function writeBuilder(
    table: string,
    sink: Recorded[],
    result: () => unknown[]
  ) {
    // One record per write; `.where(...)` lands on the row this chain pushed,
    // so a predicate can be asserted alongside the values it guards.
    const conditions: unknown[] = [];
    const builder: Record<string, unknown> = {
      values(values: Record<string, unknown>) {
        sink.push({ table, values, conditions });
        return builder;
      },
      set(values: Record<string, unknown>) {
        sink.push({ table, values, conditions });
        return builder;
      },
      where(condition: unknown) {
        conditions.push(condition);
        return builder;
      },
      returning: () => builder,
      onConflictDoNothing: () => builder,
      then(
        resolve: (rows: unknown[]) => unknown,
        reject?: (reason: unknown) => unknown
      ) {
        return Promise.resolve(result()).then(resolve, reject);
      },
    };
    return builder;
  }

  const client = {
    select: () => selectBuilder(),
    insert: (table: unknown) =>
      writeBuilder(tableName(table), state.inserts, () => state.returning),
    update: (table: unknown) =>
      writeBuilder(tableName(table), state.updates, () => state.updateReturning),
    delete: (table: unknown) =>
      writeBuilder(tableName(table), state.updates, () => []),
    transaction: async <T>(callback: (tx: unknown) => Promise<T>): Promise<T> =>
      callback(client),
  };

  return {
    state,
    schema,
    db: client,
    sendEmail: vi.fn(
      async (...args: [to: string, subject: string, html: string]) => {
        void args;
      }
    ),
    hashPassword: vi.fn(async (plain: string) => `bcrypt:${plain}`),
    verifyPassword: vi.fn(async () => true),
    checkRateLimit: vi.fn(async () => state.rateLimit),
  };
});

vi.mock("@/lib/server/db", () => ({ db: mocks.db }));
vi.mock("@/lib/server/db/schema", () => mocks.schema);
vi.mock("@auth/drizzle-adapter", () => ({ DrizzleAdapter: vi.fn(() => ({})) }));
vi.mock("drizzle-orm", () => ({
  and: (...conditions: unknown[]) => ({ op: "and", conditions }),
  asc: (column: unknown) => ({ op: "asc", column }),
  desc: (column: unknown) => ({ op: "desc", column }),
  eq: (column: unknown, value: unknown) => ({ op: "eq", column, value }),
  gt: (column: unknown, value: unknown) => ({ op: "gt", column, value }),
  isNull: (column: unknown) => ({ op: "isNull", column }),
}));
vi.mock("@/lib/server/middleware/api-auth", () => ({
  checkRateLimit: mocks.checkRateLimit,
}));
vi.mock("@/lib/server/services/email", () => ({ sendEmail: mocks.sendEmail }));
vi.mock("@/lib/server/password", () => ({
  hashPassword: mocks.hashPassword,
  verifyPassword: mocks.verifyPassword,
}));

import type { CredentialsConfig } from "next-auth/providers/credentials";
import { authOptions } from "@/lib/server/auth-options";
import { POST as forgotPassword } from "@/app/api/auth/forgot-password/route";
import { POST as registerAccount } from "@/app/api/auth/register/route";
import { POST as resetPassword } from "@/app/api/auth/reset-password/route";
import { POST as verifyEmail } from "@/app/api/auth/verify-email/route";

const USER_ID = "11111111-1111-4111-8111-111111111111";
const TEAM_A = "22222222-2222-4222-8222-222222222222";
const TEAM_B = "33333333-3333-4333-8333-333333333333";
const RAW_TOKEN = "Zm9yZ290LXBhc3N3b3JkLXRva2VuLXNhbXBsZQ";
const RAW_TOKEN_HASH = createHash("sha256").update(RAW_TOKEN, "utf8").digest("hex");

function post(path: string, body: unknown) {
  return new NextRequest(`http://localhost${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

function emailedUrl(): string {
  const html = mocks.sendEmail.mock.calls.at(-1)?.[2] as string | undefined;
  const match = html?.match(/href="([^"]+)"/);
  return match?.[1] ?? "";
}

function insertsFor(table: string) {
  return mocks.state.inserts.filter((row) => row.table === table);
}

function updatesFor(table: string) {
  return mocks.state.updates.filter((row) => row.table === table);
}

function inFuture(ms = 60_000) {
  return new Date(Date.now() + ms);
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.state.selectRows = {};
  mocks.state.selectConditions = [];
  mocks.state.inserts = [];
  mocks.state.updates = [];
  mocks.state.returning = [];
  mocks.state.updateReturning = [{ id: "row" }];
  mocks.state.rateLimit = { available: true, limited: false };
  mocks.checkRateLimit.mockImplementation(async () => mocks.state.rateLimit);
  mocks.hashPassword.mockImplementation(async (plain: string) => `bcrypt:${plain}`);
  mocks.verifyPassword.mockImplementation(async () => true);
  process.env.NEXT_PUBLIC_APP_URL = "https://plantgeo.test/";
  delete process.env.EMAIL_PROVIDER;
});

describe("password reset issuance", () => {
  it("persists only the token hash and emails the raw token", async () => {
    mocks.state.selectRows.users = [
      { id: USER_ID, email: "user@example.com", passwordHash: "bcrypt:existing" },
    ];

    const response = await forgotPassword(
      post("/api/auth/forgot-password", { email: "user@example.com" })
    );
    const bodyText = await response.text();

    expect(response.status).toBe(200);

    const url = emailedUrl();
    expect(url.startsWith("https://plantgeo.test/reset-password?token=")).toBe(true);
    const issuedToken = new URL(url).searchParams.get("token") ?? "";
    expect(issuedToken.length).toBeGreaterThan(20);

    const [record] = insertsFor("password_reset_tokens");
    expect(record).toBeDefined();
    expect(record.values.tokenHash).toBe(
      createHash("sha256").update(issuedToken, "utf8").digest("hex")
    );
    expect(JSON.stringify(record.values)).not.toContain(issuedToken);
    expect(bodyText).not.toContain(issuedToken);

    // Any previously outstanding link is burned when a new one is issued.
    expect(updatesFor("password_reset_tokens")[0]?.values).toMatchObject({
      usedAt: expect.any(Date),
    });
  });

  it("answers identically for unknown and federated-only accounts", async () => {
    mocks.state.selectRows.users = [
      { id: USER_ID, email: "user@example.com", passwordHash: "bcrypt:existing" },
    ];
    const known = await forgotPassword(
      post("/api/auth/forgot-password", { email: "user@example.com" })
    );
    const knownBody = await known.text();

    mocks.state.selectRows.users = [];
    const unknown = await forgotPassword(
      post("/api/auth/forgot-password", { email: "nobody@example.com" })
    );
    const unknownBody = await unknown.text();

    mocks.state.inserts = [];
    mocks.state.selectRows.users = [
      { id: USER_ID, email: "oauth@example.com", passwordHash: null },
    ];
    const federated = await forgotPassword(
      post("/api/auth/forgot-password", { email: "oauth@example.com" })
    );
    const federatedBody = await federated.text();

    expect(unknown.status).toBe(known.status);
    expect(federated.status).toBe(known.status);
    expect(unknownBody).toBe(knownBody);
    expect(federatedBody).toBe(knownBody);
    // No token is minted for an account that has no password to reset.
    expect(insertsFor("password_reset_tokens")).toHaveLength(0);
  });
});

describe("password reset redemption", () => {
  it("rotates the password and burns every outstanding token", async () => {
    mocks.state.selectRows.password_reset_tokens = [
      {
        userId: USER_ID,
        tokenHash: RAW_TOKEN_HASH,
        expiresAt: inFuture(),
        usedAt: null,
      },
    ];
    mocks.state.selectRows.users = [{ id: USER_ID, passwordHash: "bcrypt:old" }];

    const response = await resetPassword(
      post("/api/auth/reset-password", {
        token: RAW_TOKEN,
        password: "correct horse battery staple",
      })
    );

    expect(response.status).toBe(200);
    expect(updatesFor("users")[0]?.values).toEqual({
      passwordHash: "bcrypt:correct horse battery staple",
    });
    expect(updatesFor("password_reset_tokens")[0]?.values).toMatchObject({
      usedAt: expect.any(Date),
    });
  });

  it("rejects a token that was already redeemed", async () => {
    mocks.state.selectRows.password_reset_tokens = [
      {
        userId: USER_ID,
        tokenHash: RAW_TOKEN_HASH,
        expiresAt: inFuture(),
        usedAt: new Date(Date.now() - 1_000),
      },
    ];
    mocks.state.selectRows.users = [{ id: USER_ID, passwordHash: "bcrypt:old" }];

    const response = await resetPassword(
      post("/api/auth/reset-password", {
        token: RAW_TOKEN,
        password: "correct horse battery staple",
      })
    );

    expect(response.status).toBe(400);
    expect(updatesFor("users")).toHaveLength(0);
  });

  it("rejects an expired token", async () => {
    mocks.state.selectRows.password_reset_tokens = [
      {
        userId: USER_ID,
        tokenHash: RAW_TOKEN_HASH,
        expiresAt: new Date(Date.now() - 1_000),
        usedAt: null,
      },
    ];
    mocks.state.selectRows.users = [{ id: USER_ID, passwordHash: "bcrypt:old" }];

    const response = await resetPassword(
      post("/api/auth/reset-password", {
        token: RAW_TOKEN,
        password: "correct horse battery staple",
      })
    );

    expect(response.status).toBe(400);
    expect(updatesFor("users")).toHaveLength(0);
  });

  it("does not reveal that an account is federated-only", async () => {
    mocks.state.selectRows.password_reset_tokens = [
      {
        userId: USER_ID,
        tokenHash: RAW_TOKEN_HASH,
        expiresAt: inFuture(),
        usedAt: null,
      },
    ];
    mocks.state.selectRows.users = [{ id: USER_ID, passwordHash: null }];

    const federated = await resetPassword(
      post("/api/auth/reset-password", {
        token: RAW_TOKEN,
        password: "correct horse battery staple",
      })
    );
    const federatedBody = await federated.text();

    mocks.state.selectRows.password_reset_tokens = [];
    const unknownToken = await resetPassword(
      post("/api/auth/reset-password", {
        token: RAW_TOKEN,
        password: "correct horse battery staple",
      })
    );

    expect(federated.status).toBe(400);
    expect(federatedBody).toBe(await unknownToken.text());
    expect(updatesFor("users")).toHaveLength(0);
    expect(federatedBody).not.toContain("oauth");
  });

  it("refuses to rotate the password when the burn matched no rows", async () => {
    mocks.state.selectRows.password_reset_tokens = [
      {
        userId: USER_ID,
        tokenHash: RAW_TOKEN_HASH,
        expiresAt: inFuture(),
        usedAt: null,
      },
    ];
    mocks.state.selectRows.users = [{ id: USER_ID, passwordHash: "bcrypt:old" }];
    // A concurrent redemption already burned the token between the locked read
    // and this write, so the conditional UPDATE matches nothing.
    mocks.state.updateReturning = [];

    const response = await resetPassword(
      post("/api/auth/reset-password", {
        token: RAW_TOKEN,
        password: "correct horse battery staple",
      })
    );

    expect(response.status).toBe(400);
    expect(updatesFor("users")).toHaveLength(0);
  });
});

describe("registration", () => {
  function register(email: string) {
    return registerAccount(
      post("/api/auth/register", {
        name: "New User",
        email,
        password: "correct horse battery staple",
      })
    );
  }

  it("answers identically whether or not the address is already registered", async () => {
    mocks.state.returning = [{ id: USER_ID, email: "new@example.com" }];
    mocks.state.selectRows.users = [];
    const created = await register("new@example.com");
    const createdBody = await created.text();

    mocks.state.selectRows.users = [{ id: USER_ID }];
    const collided = await register("taken@example.com");
    const collidedBody = await collided.text();

    expect(created.status).toBe(201);
    expect(collided.status).toBe(201);
    expect(collidedBody).toBe(createdBody);
    // The acknowledgement must not carry an account identifier either.
    expect(createdBody).not.toContain(USER_ID);
  });

  it("tells the address owner about the collision instead of the caller", async () => {
    mocks.state.selectRows.users = [{ id: USER_ID }];

    const response = await register("taken@example.com");

    expect(response.status).toBe(201);
    expect(insertsFor("users")).toHaveLength(0);
    const [to, subject] = mocks.sendEmail.mock.calls.at(-1) ?? [];
    expect(to).toBe("taken@example.com");
    expect(subject).toBe("You already have a PlantGeo account");
  });

  it("answers with the acknowledgement when the insert loses a unique race", async () => {
    mocks.state.selectRows.users = [];
    mocks.state.returning = [];
    const conflict = Object.assign(new Error("duplicate key"), { code: "23505" });
    const insert = mocks.db.insert;
    mocks.db.insert = () => {
      throw conflict;
    };

    try {
      const response = await register("racy@example.com");
      expect(response.status).toBe(201);
      expect(await response.text()).toContain("Check your email");
    } finally {
      mocks.db.insert = insert;
    }
  });
});

describe("credential authorization", () => {
  // next-auth nests the caller's provider options instead of spreading them,
  // so the configured authorize only reaches the top level after the internal
  // parseProviders() pass that never runs here.
  const configured = authOptions.providers.find(
    (provider) => provider.id === "credentials"
  ) as unknown as
    | { options?: { authorize?: CredentialsConfig["authorize"] } }
    | undefined;
  const authorize = configured?.options?.authorize;

  it("looks the account up by its normalized address", async () => {
    if (!authorize) throw new Error("credentials provider is not configured");
    mocks.state.selectRows.users = [
      {
        id: USER_ID,
        email: "alice@ex.com",
        name: "Alice",
        passwordHash: "bcrypt:existing",
        platformRole: "contributor",
      },
    ];

    const user = await authorize(
      { email: "  Alice@Ex.com  ", password: "hunter2hunter2" },
      // The provider never reads the request; NextAuth's type still demands it.
      {} as Parameters<NonNullable<CredentialsConfig["authorize"]>>[1]
    );

    expect(user).toMatchObject({ id: USER_ID });
    const lookups = mocks.state.selectConditions as Array<{ value?: unknown }>;
    expect(lookups.some((c) => c?.value === "alice@ex.com")).toBe(true);
    expect(lookups.some((c) => c?.value === "  Alice@Ex.com  ")).toBe(false);
  });
});

describe("email verification", () => {
  it("marks the address verified once and refuses a replay", async () => {
    mocks.state.selectRows.email_verification_tokens = [
      {
        userId: USER_ID,
        tokenHash: RAW_TOKEN_HASH,
        expiresAt: inFuture(),
        usedAt: null,
      },
    ];

    const first = await verifyEmail(
      post("/api/auth/verify-email", { token: RAW_TOKEN })
    );
    expect(first.status).toBe(200);
    expect(updatesFor("users")[0]?.values).toMatchObject({
      emailVerified: expect.any(Date),
    });
    expect(updatesFor("email_verification_tokens")[0]?.values).toMatchObject({
      usedAt: expect.any(Date),
    });

    mocks.state.updates = [];
    mocks.state.selectRows.email_verification_tokens = [
      {
        userId: USER_ID,
        tokenHash: RAW_TOKEN_HASH,
        expiresAt: inFuture(),
        usedAt: new Date(),
      },
    ];
    const replay = await verifyEmail(
      post("/api/auth/verify-email", { token: RAW_TOKEN })
    );
    expect(replay.status).toBe(400);
    expect(updatesFor("users")).toHaveLength(0);
  });
});

describe("session organization context", () => {
  const jwtCallback = authOptions.callbacks?.jwt;
  const sessionCallback = authOptions.callbacks?.session;

  function membershipRows(activeTeamId: string | null) {
    return [
      {
        platformRole: "contributor",
        activeTeamId,
        memberTeamId: TEAM_A,
        memberRole: "owner",
      },
      {
        platformRole: "contributor",
        activeTeamId,
        memberTeamId: TEAM_B,
        memberRole: "viewer",
      },
    ];
  }

  function jwtParams(overrides: Record<string, unknown> = {}) {
    return {
      token: {
        id: USER_ID,
        sub: USER_ID,
        platformRole: "contributor",
        activeTeamId: null,
        activeTeamRole: null,
      },
      user: { id: USER_ID, email: "user@example.com", name: "User" },
      account: null,
      ...overrides,
    } as unknown as Parameters<NonNullable<typeof jwtCallback>>[0];
  }

  function oauthJwtParams(provider: string, profile: unknown) {
    return jwtParams({
      account: { provider, type: "oauth", providerAccountId: "ext-1" },
      profile,
    });
  }

  function emailVerifiedStamps() {
    return updatesFor("users").filter((row) => "emailVerified" in row.values);
  }

  describe("provider-asserted email verification", () => {
    beforeEach(() => {
      mocks.state.selectRows.users = membershipRows(TEAM_A);
    });

    it("stamps emailVerified when Google asserts the address is verified", async () => {
      if (!jwtCallback) throw new Error("JWT callback is not configured");

      await jwtCallback(oauthJwtParams("google", { email_verified: true }));

      const [stamp] = emailVerifiedStamps();
      expect(stamp?.values).toMatchObject({ emailVerified: expect.any(Date) });
    });

    it("never overwrites a timestamp that is already set", async () => {
      if (!jwtCallback) throw new Error("JWT callback is not configured");

      await jwtCallback(oauthJwtParams("google", { email_verified: true }));

      // The guarantee is the predicate, not a read-then-write: the column is
      // only assigned where it is still null.
      const [stamp] = emailVerifiedStamps();
      expect(JSON.stringify(stamp?.conditions)).toContain(
        JSON.stringify({ op: "isNull", column: "users.emailVerified" })
      );
    });

    it("leaves the address unverified when Google says it is not", async () => {
      if (!jwtCallback) throw new Error("JWT callback is not configured");

      await jwtCallback(oauthJwtParams("google", { email_verified: false }));

      expect(emailVerifiedStamps()).toHaveLength(0);
    });

    it("ignores a provider that makes no verification claim", async () => {
      if (!jwtCallback) throw new Error("JWT callback is not configured");

      await jwtCallback(oauthJwtParams("okta", { email_verified: true }));

      expect(emailVerifiedStamps()).toHaveLength(0);
    });

    it("leaves a credential registration unverified", async () => {
      if (!jwtCallback) throw new Error("JWT callback is not configured");
      mocks.state.selectRows.users = [];
      mocks.state.returning = [{ id: USER_ID, email: "new@example.com" }];

      await registerAccount(
        post("/api/auth/register", {
          email: "new@example.com",
          password: "correct horse battery staple",
        })
      );
      mocks.state.selectRows.users = membershipRows(TEAM_A);
      await jwtCallback(
        jwtParams({
          account: { provider: "credentials", type: "credentials" },
          profile: undefined,
        })
      );

      expect(insertsFor("users")[0]?.values.emailVerified).toBeUndefined();
      expect(emailVerifiedStamps()).toHaveLength(0);
    });
  });

  it("falls back to the earliest membership when the stored team is stale", async () => {
    if (!jwtCallback) throw new Error("JWT callback is not configured");
    mocks.state.selectRows.users = membershipRows("44444444-4444-4444-8444-444444444444");

    const token = await jwtCallback(jwtParams());

    expect(token).toMatchObject({
      id: USER_ID,
      platformRole: "contributor",
      activeTeamId: TEAM_A,
      activeTeamRole: "owner",
    });
  });

  it("keeps a stored team the user still belongs to", async () => {
    if (!jwtCallback) throw new Error("JWT callback is not configured");
    mocks.state.selectRows.users = membershipRows(TEAM_B);

    const token = await jwtCallback(jwtParams());

    expect(token).toMatchObject({ activeTeamId: TEAM_B, activeTeamRole: "viewer" });
  });

  it("honors an update trigger that switches to another membership", async () => {
    if (!jwtCallback) throw new Error("JWT callback is not configured");
    mocks.state.selectRows.users = membershipRows(TEAM_A);

    const token = await jwtCallback(
      jwtParams({ trigger: "update", session: { activeTeamId: TEAM_B } })
    );

    expect(token).toMatchObject({ activeTeamId: TEAM_B, activeTeamRole: "viewer" });
  });

  it("ignores an update trigger for a team the user does not belong to", async () => {
    if (!jwtCallback) throw new Error("JWT callback is not configured");
    mocks.state.selectRows.users = membershipRows(TEAM_A);

    const token = await jwtCallback(
      jwtParams({
        trigger: "update",
        session: { activeTeamId: "55555555-5555-4555-8555-555555555555" },
      })
    );

    expect(token).toMatchObject({ activeTeamId: TEAM_A, activeTeamRole: "owner" });
  });

  it("reports no organization for a user without memberships", async () => {
    if (!jwtCallback) throw new Error("JWT callback is not configured");
    mocks.state.selectRows.users = [
      {
        platformRole: "contributor",
        activeTeamId: null,
        memberTeamId: null,
        memberRole: null,
      },
    ];

    const token = await jwtCallback(jwtParams());

    expect(token).toMatchObject({ activeTeamId: null, activeTeamRole: null });
  });

  it("revokes the session when the user row is gone", async () => {
    if (!jwtCallback) throw new Error("JWT callback is not configured");
    mocks.state.selectRows.users = [];

    await expect(jwtCallback(jwtParams())).rejects.toThrow(
      "Session identity is no longer active"
    );
  });

  it("exposes the organization context on the session", async () => {
    if (!sessionCallback) throw new Error("session callback is not configured");

    const session = await sessionCallback({
      session: { user: { email: "user@example.com" }, expires: "2099-01-01" },
      token: {
        id: USER_ID,
        platformRole: "contributor",
        activeTeamId: TEAM_A,
        activeTeamRole: "owner",
      },
    } as unknown as Parameters<NonNullable<typeof sessionCallback>>[0]);

    expect(session.user).toMatchObject({
      id: USER_ID,
      platformRole: "contributor",
      activeTeamId: TEAM_A,
      activeTeamRole: "owner",
    });
  });
});
