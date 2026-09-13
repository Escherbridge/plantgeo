import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/server/db", () => ({ db: {} }));
vi.mock("@/lib/server/auth", () => ({ getServerSession: vi.fn() }));

import type { Context } from "@/lib/server/trpc/init";
import {
  interventionSocialRouter,
  MAX_COMMENT_BODY_LENGTH,
} from "@/lib/server/trpc/routers/intervention-social";

const AUTHOR_USER_ID = "22222222-2222-4222-8222-222222222222";
const OTHER_USER_ID = "44444444-4444-4444-8444-444444444444";
const MODERATOR_USER_ID = "55555555-5555-4555-8555-555555555555";
const TEAM_ID = "11111111-1111-4111-8111-111111111111";
const FEATURE_ID = "66666666-6666-4666-8666-666666666666";
const COMMENT_ID = "77777777-7777-4777-8777-777777777777";
const LIKE_ID = "88888888-8888-4888-8888-888888888888";

type Row = Record<string, unknown>;
type BuilderCall = { method: string; args: unknown[] };

/** Chainable drizzle stand-in, mirroring `src/__tests__/trpc/interventions.test.ts`. */
function createQueryStub(queue: Row[][], calls: BuilderCall[]): unknown {
  const proxy: unknown = new Proxy(() => {}, {
    get(_target, property) {
      if (property === "then") {
        return (resolve: (rows: Row[]) => unknown) => resolve(queue.shift() ?? []);
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

/**
 * Every string reachable from a recorded builder argument. Drizzle's `SQL`
 * carries `Column` instances (whose `name` is the physical column) and
 * `StringChunk`s (the literal operator text), so a predicate built from
 * `isNull(featureComments.deletedAt)` is observable without a database.
 */
function reachableStrings(value: unknown, seen = new WeakSet<object>()): string[] {
  if (typeof value === "string") return [value];
  if (typeof value !== "object" || value === null) return [];
  if (seen.has(value)) return [];
  seen.add(value);
  return Object.values(value as Record<string, unknown>).flatMap((child) =>
    reachableStrings(child, seen)
  );
}

function predicateStrings(calls: BuilderCall[]): string[] {
  return calls
    .filter((call) => call.method === "where")
    .flatMap((call) => call.args.flatMap((arg) => reachableStrings(arg)));
}

function sessionFor(
  userId: string,
  platformRole = "contributor"
): NonNullable<Context["session"]> {
  return {
    expires: "2099-01-01T00:00:00.000Z",
    user: { id: userId, platformRole } as NonNullable<Context["session"]>["user"],
  };
}

function callerWith(
  script: Row[][],
  session: Context["session"] | null,
  calls: BuilderCall[] = []
) {
  return interventionSocialRouter.createCaller({
    db: createScriptedDatabase(script, calls),
    session,
  } as Context);
}

/** The visibility probe's projection of one `geo.features` row. */
function featureVisibilityRow(overrides: Row = {}): Row[] {
  return [
    {
      id: FEATURE_ID,
      status: "published",
      submittedByUserId: AUTHOR_USER_ID,
      submittedByTeamId: null,
      publicationConsent: "true",
      ...overrides,
    },
  ];
}

const PUBLISHED = featureVisibilityRow();
/** Another contributor's draft, no consent recorded: invisible to everyone else. */
const PRIVATE_DRAFT = featureVisibilityRow({
  status: "pending_review",
  submittedByUserId: AUTHOR_USER_ID,
  publicationConsent: "false",
});

beforeEach(() => {
  vi.clearAllMocks();
});

describe("interventionSocial.toggleLike", () => {
  it("rejects an unauthenticated caller", async () => {
    const caller = callerWith([], null);
    await expect(caller.toggleLike({ featureId: FEATURE_ID })).rejects.toMatchObject(
      { code: "UNAUTHORIZED" }
    );
  });

  it("rejects a signed-in caller without a contributor platform role", async () => {
    const caller = callerWith([], sessionFor(OTHER_USER_ID, "reader"));
    await expect(caller.toggleLike({ featureId: FEATURE_ID })).rejects.toMatchObject(
      { code: "FORBIDDEN" }
    );
  });

  it("likes a published feature the caller had not liked yet", async () => {
    const caller = callerWith(
      [PUBLISHED, [], [], [{ total: 1 }]],
      sessionFor(OTHER_USER_ID)
    );

    await expect(caller.toggleLike({ featureId: FEATURE_ID })).resolves.toEqual({
      liked: true,
      count: 1,
    });
  });

  it("unlikes on the second call, returning the opposite state", async () => {
    const caller = callerWith(
      [PUBLISHED, [{ id: LIKE_ID }], [], [{ total: 0 }]],
      sessionFor(OTHER_USER_ID)
    );

    await expect(caller.toggleLike({ featureId: FEATURE_ID })).resolves.toEqual({
      liked: false,
      count: 0,
    });
  });

  it("lets the submitter like their own pending-review draft", async () => {
    const caller = callerWith(
      [PRIVATE_DRAFT, [], [], [{ total: 1 }]],
      sessionFor(AUTHOR_USER_ID)
    );

    await expect(caller.toggleLike({ featureId: FEATURE_ID })).resolves.toEqual({
      liked: true,
      count: 1,
    });
  });

  it("lets a member of the submitting workspace like a team draft", async () => {
    const caller = callerWith(
      [
        featureVisibilityRow({
          status: "pending_review",
          submittedByTeamId: TEAM_ID,
          publicationConsent: "false",
        }),
        [{ userId: OTHER_USER_ID }], // membership re-read
        [],
        [],
        [{ total: 1 }],
      ],
      sessionFor(OTHER_USER_ID)
    );

    await expect(caller.toggleLike({ featureId: FEATURE_ID })).resolves.toEqual({
      liked: true,
      count: 1,
    });
  });

  it("lets any signed-in contributor like a consented review-queue proposal", async () => {
    const caller = callerWith(
      [
        featureVisibilityRow({
          status: "pending_review",
          publicationConsent: "true",
        }),
        [],
        [],
        [{ total: 1 }],
      ],
      sessionFor(OTHER_USER_ID)
    );

    await expect(caller.toggleLike({ featureId: FEATURE_ID })).resolves.toEqual({
      liked: true,
      count: 1,
    });
  });

  // The NFR-2 negative case: an unrelated contributor guessing a feature id.
  it("refuses an unrelated caller on a pending-review draft without consent", async () => {
    const calls: BuilderCall[] = [];
    const caller = callerWith([PRIVATE_DRAFT], sessionFor(OTHER_USER_ID), calls);

    await expect(caller.toggleLike({ featureId: FEATURE_ID })).rejects.toMatchObject(
      { code: "NOT_FOUND" }
    );
    // Not merely a refused response: nothing was written.
    expect(calls.some((call) => call.method === "values")).toBe(false);
  });

  it("refuses an unrelated caller on a team draft they are not a member of", async () => {
    const caller = callerWith(
      [
        featureVisibilityRow({
          status: "pending_review",
          submittedByTeamId: TEAM_ID,
          publicationConsent: "false",
        }),
        [], // no membership row
      ],
      sessionFor(OTHER_USER_ID)
    );

    await expect(caller.toggleLike({ featureId: FEATURE_ID })).rejects.toMatchObject(
      { code: "NOT_FOUND" }
    );
  });

  it("refuses a feature id that does not exist, as NOT_FOUND rather than a leak", async () => {
    const caller = callerWith([[]], sessionFor(OTHER_USER_ID));
    await expect(caller.toggleLike({ featureId: FEATURE_ID })).rejects.toMatchObject(
      { code: "NOT_FOUND" }
    );
  });
});

describe("interventionSocial.listComments", () => {
  const commentRow = (overrides: Row = {}): Row => ({
    id: COMMENT_ID,
    featureId: FEATURE_ID,
    authorUserId: AUTHOR_USER_ID,
    body: "Worth pairing this with the upstream riparian buffer.",
    createdAt: new Date("2026-09-01T00:00:00.000Z"),
    ...overrides,
  });

  it("rejects an unauthenticated caller", async () => {
    const caller = callerWith([], null);
    await expect(
      caller.listComments({ featureId: FEATURE_ID })
    ).rejects.toMatchObject({ code: "UNAUTHORIZED" });
  });

  it("returns one feature's comments with a null next offset on the last page", async () => {
    const caller = callerWith(
      [PUBLISHED, [commentRow()]],
      sessionFor(OTHER_USER_ID)
    );

    const result = await caller.listComments({ featureId: FEATURE_ID, limit: 50 });

    expect(result.comments).toHaveLength(1);
    expect(result.comments[0]).toMatchObject({ featureId: FEATURE_ID });
    expect(result.nextOffset).toBeNull();
  });

  it("hands back the next offset when the page is full", async () => {
    const caller = callerWith(
      [PUBLISHED, [commentRow(), commentRow({ id: "other" })]],
      sessionFor(OTHER_USER_ID)
    );

    const result = await caller.listComments({
      featureId: FEATURE_ID,
      limit: 2,
      offset: 4,
    });

    expect(result.nextOffset).toBe(6);
  });

  it("filters soft-deleted comments out of the query itself", async () => {
    const calls: BuilderCall[] = [];
    const caller = callerWith(
      [PUBLISHED, []],
      sessionFor(OTHER_USER_ID),
      calls
    );

    await caller.listComments({ featureId: FEATURE_ID });

    const predicates = predicateStrings(calls);
    expect(predicates).toContain("deleted_at");
    expect(predicates.join(" ")).toMatch(/is null/i);
  });

  it("refuses an unrelated caller on a pending-review draft without consent", async () => {
    const caller = callerWith([PRIVATE_DRAFT], sessionFor(OTHER_USER_ID));
    await expect(
      caller.listComments({ featureId: FEATURE_ID })
    ).rejects.toMatchObject({ code: "NOT_FOUND" });
  });
});

describe("interventionSocial.postComment", () => {
  const posted = (body: string): Row[] => [
    {
      id: COMMENT_ID,
      featureId: FEATURE_ID,
      authorUserId: AUTHOR_USER_ID,
      body,
      createdAt: new Date("2026-09-01T00:00:00.000Z"),
    },
  ];

  it("rejects a signed-in caller without a contributor platform role", async () => {
    const caller = callerWith([], sessionFor(OTHER_USER_ID, "reader"));
    await expect(
      caller.postComment({ featureId: FEATURE_ID, body: "hello" })
    ).rejects.toMatchObject({ code: "FORBIDDEN" });
  });

  it("attributes the comment to the acting user", async () => {
    const calls: BuilderCall[] = [];
    const caller = callerWith(
      [PUBLISHED, posted("hello")],
      sessionFor(AUTHOR_USER_ID),
      calls
    );

    await caller.postComment({ featureId: FEATURE_ID, body: "hello" });

    const written = calls.find((call) => call.method === "values")?.args[0] as Row;
    expect(written).toMatchObject({
      featureId: FEATURE_ID,
      authorUserId: AUTHOR_USER_ID,
      body: "hello",
    });
  });

  it("rejects an empty body", async () => {
    const caller = callerWith([], sessionFor(AUTHOR_USER_ID));
    await expect(
      caller.postComment({ featureId: FEATURE_ID, body: "   " })
    ).rejects.toMatchObject({ code: "BAD_REQUEST" });
  });

  it("rejects a body over the same bound submitIntervention's description uses", async () => {
    const caller = callerWith([], sessionFor(AUTHOR_USER_ID));
    await expect(
      caller.postComment({
        featureId: FEATURE_ID,
        body: "x".repeat(MAX_COMMENT_BODY_LENGTH + 1),
      })
    ).rejects.toMatchObject({ code: "BAD_REQUEST" });
  });

  it("accepts a body exactly at the bound", async () => {
    const body = "x".repeat(MAX_COMMENT_BODY_LENGTH);
    const caller = callerWith([PUBLISHED, posted(body)], sessionFor(AUTHOR_USER_ID));

    await expect(
      caller.postComment({ featureId: FEATURE_ID, body })
    ).resolves.toMatchObject({ id: COMMENT_ID });
  });

  it("refuses an unrelated caller on a pending-review draft without consent", async () => {
    const calls: BuilderCall[] = [];
    const caller = callerWith([PRIVATE_DRAFT], sessionFor(OTHER_USER_ID), calls);

    await expect(
      caller.postComment({ featureId: FEATURE_ID, body: "hello" })
    ).rejects.toMatchObject({ code: "NOT_FOUND" });
    expect(calls.some((call) => call.method === "values")).toBe(false);
  });
});

describe("interventionSocial.deleteComment", () => {
  const existingComment = (overrides: Row = {}): Row[] => [
    {
      id: COMMENT_ID,
      authorUserId: AUTHOR_USER_ID,
      deletedAt: null,
      ...overrides,
    },
  ];
  const softDeleted: Row[] = [
    { id: COMMENT_ID, deletedAt: new Date("2026-09-02T00:00:00.000Z") },
  ];

  it("lets the comment's own author delete it", async () => {
    const calls: BuilderCall[] = [];
    const caller = callerWith(
      [existingComment(), softDeleted],
      sessionFor(AUTHOR_USER_ID),
      calls
    );

    await expect(
      caller.deleteComment({ commentId: COMMENT_ID })
    ).resolves.toMatchObject({ id: COMMENT_ID });

    // Soft delete: the row is stamped, never removed.
    expect(calls.some((call) => call.method === "set")).toBe(true);
    const stamped = calls.find((call) => call.method === "set")?.args[0] as Row;
    expect(stamped.deletedAt).toBeInstanceOf(Date);
    expect(stamped.deletedByUserId).toBe(AUTHOR_USER_ID);
  });

  it("lets an expert delete another author's comment", async () => {
    const caller = callerWith(
      [existingComment(), softDeleted],
      sessionFor(MODERATOR_USER_ID, "expert")
    );

    await expect(
      caller.deleteComment({ commentId: COMMENT_ID })
    ).resolves.toMatchObject({ id: COMMENT_ID });
  });

  it("lets an admin delete another author's comment", async () => {
    const caller = callerWith(
      [existingComment(), softDeleted],
      sessionFor(MODERATOR_USER_ID, "admin")
    );

    await expect(
      caller.deleteComment({ commentId: COMMENT_ID })
    ).resolves.toMatchObject({ id: COMMENT_ID });
  });

  it("refuses a contributor who is neither the author nor a moderator", async () => {
    const calls: BuilderCall[] = [];
    const caller = callerWith(
      [existingComment()],
      sessionFor(OTHER_USER_ID),
      calls
    );

    await expect(
      caller.deleteComment({ commentId: COMMENT_ID })
    ).rejects.toMatchObject({ code: "FORBIDDEN" });
    expect(calls.some((call) => call.method === "set")).toBe(false);
  });

  it("refuses an already-deleted comment", async () => {
    const caller = callerWith(
      [existingComment({ deletedAt: new Date("2026-09-02T00:00:00.000Z") })],
      sessionFor(AUTHOR_USER_ID)
    );

    await expect(
      caller.deleteComment({ commentId: COMMENT_ID })
    ).rejects.toMatchObject({ code: "NOT_FOUND" });
  });

  it("errors when the UPDATE matches nothing, rather than resolving as success", async () => {
    const caller = callerWith([existingComment(), []], sessionFor(AUTHOR_USER_ID));

    await expect(
      caller.deleteComment({ commentId: COMMENT_ID })
    ).rejects.toMatchObject({ code: "NOT_FOUND" });
  });

  it("drops the comment from the next listComments page", async () => {
    const deleteCaller = callerWith(
      [existingComment(), softDeleted],
      sessionFor(AUTHOR_USER_ID)
    );
    await deleteCaller.deleteComment({ commentId: COMMENT_ID });

    // The soft-deleted row is filtered by the `deleted_at IS NULL` predicate
    // asserted in the listComments suite, so the next page comes back empty.
    const listCaller = callerWith([PUBLISHED, []], sessionFor(AUTHOR_USER_ID));
    const result = await listCaller.listComments({ featureId: FEATURE_ID });

    expect(result.comments).toHaveLength(0);
  });
});
