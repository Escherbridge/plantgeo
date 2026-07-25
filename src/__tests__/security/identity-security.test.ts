import { describe, expect, it } from "vitest";
import { getTableColumns, getTableName } from "drizzle-orm";
import { PgDialect } from "drizzle-orm/pg-core";
import { authAdapterTables } from "@/lib/server/auth-schema";
import {
  API_KEY_LAST_USED_REFRESH_MS,
  hasRequiredApiKeyPermission,
  personalApiKeyIssuanceSchema,
  shouldRefreshApiKeyLastUsed,
} from "@/lib/server/api-keys";
import {
  canInviteTeamRole,
  isTeamEditorRole,
  wouldRemoveLastOwner,
} from "@/lib/server/security/access-control";
import { parseBoundingBox } from "@/lib/server/security/bbox";
import {
  featureVisibilityCondition,
  layerVisibilityCondition,
} from "@/lib/server/security/layer-access";
import {
  isUniqueConstraintViolation,
  registrationRateLimitKey,
  registrationSchema,
} from "@/lib/server/security/registration";

const dialect = new PgDialect();

describe("identity and visibility policies", () => {
  it("maps NextAuth to the repository's actual adapter tables", () => {
    expect(getTableName(authAdapterTables.usersTable)).toBe("users");
    expect(getTableName(authAdapterTables.accountsTable)).toBe("accounts");
    expect(getTableName(authAdapterTables.sessionsTable)).toBe("sessions");
    expect(getTableName(authAdapterTables.verificationTokensTable)).toBe(
      "verification_tokens"
    );
    expect(getTableColumns(authAdapterTables.usersTable)).toMatchObject({
      emailVerified: expect.anything(),
      image: expect.anything(),
    });
  });

  it("keeps anonymous feature reads public and published", () => {
    const featureQuery = dialect.sqlToQuery(featureVisibilityCondition({}));
    expect(featureQuery.sql).toContain('"geo"."layers"."is_public"');
    expect(featureQuery.sql).toContain('"geo"."features"."status"');
    expect(featureQuery.params).toEqual([true, "published"]);

    const layerQuery = dialect.sqlToQuery(layerVisibilityCondition({}));
    expect(layerQuery.sql).toContain('"geo"."layers"."is_public"');
    expect(layerQuery.params).toEqual([true]);
  });

  it("adds only the authenticated team scope to private visibility", () => {
    const query = dialect.sqlToQuery(
      featureVisibilityCondition({ teamId: "11111111-1111-4111-8111-111111111111" })
    );
    expect(query.sql).toContain('"geo"."layers"."team_id"');
    expect(query.params).toContain("11111111-1111-4111-8111-111111111111");
    expect(query.sql).not.toContain("team_members");
  });

  it("enforces editor and delegation ceilings", () => {
    expect(isTeamEditorRole("owner")).toBe(true);
    expect(isTeamEditorRole("member")).toBe(true);
    expect(isTeamEditorRole("viewer")).toBe(false);
    expect(canInviteTeamRole("member", "viewer")).toBe(true);
    expect(canInviteTeamRole("member", "member")).toBe(false);
    expect(canInviteTeamRole("member", "owner")).toBe(false);
    expect(canInviteTeamRole("owner", "owner")).toBe(false);
  });

  it("prevents removal or demotion of the last owner", () => {
    expect(wouldRemoveLastOwner("owner", null, 1)).toBe(true);
    expect(wouldRemoveLastOwner("owner", "member", 1)).toBe(true);
    expect(wouldRemoveLastOwner("owner", "member", 2)).toBe(false);
    expect(wouldRemoveLastOwner("member", null, 1)).toBe(false);
  });

  it("validates ordered WGS84 bounding boxes", () => {
    expect(parseBoundingBox("-105,39,-104,40")).toEqual([
      -105, 39, -104, 40,
    ]);
    expect(parseBoundingBox("-104,39,-105,40")).toBeNull();
    expect(parseBoundingBox("-181,39,-104,40")).toBeNull();
    expect(parseBoundingBox("NaN,39,-104,40")).toBeNull();
  });
});

describe("credential boundary policies", () => {
  it("never promotes an explicitly empty API-key scope", () => {
    expect(
      personalApiKeyIssuanceSchema.safeParse({ permissions: [] }).success
    ).toBe(false);
    expect(hasRequiredApiKeyPermission([], "read:features")).toBe(false);
    expect(
      personalApiKeyIssuanceSchema.parse({}).permissions.length
    ).toBeGreaterThan(0);
  });

  it("throttles API-key last-used writes", () => {
    const now = new Date("2026-07-20T12:00:00.000Z");
    expect(shouldRefreshApiKeyLastUsed(null, now)).toBe(true);
    expect(shouldRefreshApiKeyLastUsed(new Date(now.getTime() - 1_000), now)).toBe(
      false
    );
    expect(
      shouldRefreshApiKeyLastUsed(
        new Date(now.getTime() - API_KEY_LAST_USED_REFRESH_MS),
        now
      )
    ).toBe(true);
  });

  it("normalizes email and rejects bcrypt-truncated passwords", () => {
    const parsed = registrationSchema.parse({
      email: "  USER@Example.COM ",
      password: "correct horse battery staple",
    });
    expect(parsed.email).toBe("user@example.com");
    expect(
      registrationSchema.safeParse({
        email: "user@example.com",
        password: "x".repeat(73),
      }).success
    ).toBe(false);
    expect(
      registrationSchema.safeParse({
        email: "user@example.com",
        password: "🌲".repeat(19),
      }).success
    ).toBe(false);
  });

  it("hashes registration limiter identities and recognizes unique races", () => {
    const request = new Request("https://plantgeo.test/api/auth/register", {
      headers: { "x-forwarded-for": "203.0.113.9, 10.0.0.1" },
    });
    const key = registrationRateLimitKey(request);
    expect(key).toMatch(/^auth:register:[a-f0-9]{64}$/);
    expect(key).not.toContain("203.0.113.9");
    expect(isUniqueConstraintViolation({ code: "23505" })).toBe(true);
    expect(isUniqueConstraintViolation({ code: "40001" })).toBe(false);
  });
});
