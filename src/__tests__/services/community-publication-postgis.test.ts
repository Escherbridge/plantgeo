// @vitest-environment node
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { drizzle } from "drizzle-orm/postgres-js";
import type { Session } from "next-auth";
import { createCommunityDatabase } from "../../../e2e/community-publication-db.mjs";
import * as schema from "@/lib/server/db/schema";
import { interventionsRouter } from "@/lib/server/trpc/routers/interventions";
import { contributionsRouter } from "@/lib/server/trpc/routers/contributions";

vi.mock("@/lib/server/auth", () => ({ getServerSession: vi.fn() }));
vi.mock("@/lib/server/db", () => ({ db: {} }));

const enabled = Boolean(process.env.COMMUNITY_TEST_ADMIN_URL);
const polygon = { type: "Polygon" as const, coordinates: [[[-105.05, 40], [-105.01, 40], [-105.01, 40.04], [-105.05, 40.04], [-105.05, 40]]] };
const input = { name: "Synthetic boundary acceptance", type: "reforestation" as const, geometry: polygon, publicationConsent: true as const };

describe.skipIf(!enabled)("community publication with real local PostGIS", () => {
  let fixture: Awaited<ReturnType<typeof createCommunityDatabase>>;
  let db: ReturnType<typeof drizzle<typeof schema>>;
  const session = (role: string): Session => ({ user: { id: fixture.identities[role], platformRole: role, activeTeamId: null, activeTeamRole: null }, expires: "2099-01-01" });
  const interventions = (role = "contributor") => interventionsRouter.createCaller({ db, session: session(role) });
  const contributions = (role = "expert") => contributionsRouter.createCaller({ db, session: session(role) });
  const reviewInput = async (featureId: string) => {
    const row = await contributions().reviewContribution({ featureId });
    expect(row).not.toBeNull();
    return { featureId, expectedReviewVersion: row!.reviewVersion };
  };
  const tile = async () => {
    const z = 10;
    const x = Math.floor((-105.03 + 180) / 360 * 2 ** z);
    const y = Math.floor((1 - Math.asinh(Math.tan(40.02 * Math.PI / 180)) / Math.PI) / 2 * 2 ** z);
    const [row] = await fixture.sql`SELECT geo.intervention_tiles(${z},${x},${y}) AS tile`;
    return row.tile as Buffer;
  };

  beforeAll(async () => {
    fixture = await createCommunityDatabase(process.env.COMMUNITY_TEST_ADMIN_URL);
    db = drizzle(fixture.sql, { schema });
    console.info("Community PostGIS acceptance database:", new URL(fixture.url).pathname, fixture.version);
  }, 30_000);
  beforeEach(async () => { await fixture.sql`DELETE FROM geo.features`; });
  afterAll(async () => { await fixture?.dispose(); });

  it("holds a contributor Polygon out of the public tile until expert publication, then persists the visible outcome", async () => {
    expect((await tile()).length).toBe(0);
    const submitted = await interventions().submitIntervention(input);
    expect(submitted.status).toBe("pending_review");
    const [stored] = await fixture.sql`SELECT ST_GeometryType(geom) AS type, ST_IsValid(geom) AS valid FROM geo.features WHERE id=${submitted.id}`;
    expect(stored).toEqual({ type: "ST_Polygon", valid: true });
    expect((await tile()).length).toBe(0);
    expect((await interventions().listMySubmissions({})).map((row) => row.id)).toContain(submitted.id);
    expect((await contributions().listPendingReview()).map((row) => row.id)).toContain(submitted.id);
    expect((await contributions().publishContribution(await reviewInput(submitted.id)))?.status).toBe("published");
    expect((await tile()).includes(Buffer.from(input.name))).toBe(true);
    expect((await interventions().listMySubmissions({}))[0].status).toBe("published");
    expect(await contributions().listPendingReview()).toHaveLength(0);
  });

  it("also exposes an explicitly submitted Point only after publication", async () => {
    const submitted = await interventions().submitIntervention({ ...input, name: "Synthetic explicit point", geometry: { type: "Point", coordinates: [-105.03, 40.02] } });
    expect((await tile()).length).toBe(0);
    await contributions().publishContribution(await reviewInput(submitted.id));
    expect((await tile()).includes(Buffer.from("Synthetic explicit point"))).toBe(true);
  });

  it("denies anonymous/viewer writes, contributor publication, malformed geometry, and absent consent without persisting rows", async () => {
    await expect(interventionsRouter.createCaller({ db, session: null }).submitIntervention(input)).rejects.toMatchObject({ code: "UNAUTHORIZED" });
    await expect(interventions("viewer").submitIntervention(input)).rejects.toMatchObject({ code: "FORBIDDEN" });
    await expect(contributions("contributor").publishContribution({ featureId: crypto.randomUUID(), expectedReviewVersion: "0".repeat(64) })).rejects.toMatchObject({ code: "FORBIDDEN" });
    await expect(interventions().submitIntervention({ ...input, geometry: { type: "Polygon", coordinates: [[[-105, 40], [-104, 40], [-104, 41]]] } })).rejects.toMatchObject({ code: "BAD_REQUEST" });
    for (const coordinates of [
      [[-105, 40], [-104, 41], [-105, 41], [-104, 40], [-105, 40]],
      [[-105, 40], [-104, 40], [-103, 40], [-105, 40]],
    ]) {
      await expect(interventions().submitIntervention({ ...input, geometry: { type: "Polygon", coordinates: [coordinates] } })).rejects.toMatchObject({ code: "BAD_REQUEST" });
    }
    const withoutConsent = { ...input, publicationConsent: false } as unknown as typeof input;
    await expect(interventions().submitIntervention(withoutConsent)).rejects.toMatchObject({ code: "BAD_REQUEST" });
    expect(await fixture.sql`SELECT id FROM geo.features`).toHaveLength(0);
  });

  it("re-reads workspace access, isolates authors, and rejects a removed member's revision", async () => {
    const [team] = await fixture.sql`INSERT INTO public.teams (name) VALUES ('Synthetic acceptance workspace') RETURNING id`;
    const author = fixture.identities.contributor;
    await fixture.sql`INSERT INTO public.team_members (team_id,user_id,team_role) VALUES (${team.id},${author},'member')`;
    const submitted = await interventions().submitIntervention({ ...input, teamId: team.id });
    expect(await interventions("expert").listMySubmissions({})).toHaveLength(0);
    await expect(interventions("expert").listMySubmissions({ teamId: team.id })).rejects.toMatchObject({ code: "NOT_FOUND" });
    await fixture.sql`INSERT INTO public.team_members (team_id,user_id,team_role) VALUES (${team.id},${fixture.identities.expert},'viewer')`;
    expect(await interventions("expert").listMySubmissions({ teamId: team.id })).toHaveLength(1);
    await expect(interventions("expert").submitIntervention({ ...input, teamId: team.id })).rejects.toMatchObject({ code: "NOT_FOUND" });
    await contributions().requestRevisionContribution({ ...await reviewInput(submitted.id), reviewNote: "Clarify the site boundary" });
    await fixture.sql`DELETE FROM public.team_members WHERE team_id=${team.id} AND user_id=${author}`;
    await expect(interventions().reviseIntervention({ ...input, featureId: submitted.id })).rejects.toMatchObject({ code: "NOT_FOUND" });
    expect((await tile()).length).toBe(0);
  });

  it("keeps rejection and revision off the map until the author revises and the expert reviews again", async () => {
    for (const decision of ["rejectContribution", "requestRevisionContribution"] as const) {
      await fixture.sql`DELETE FROM geo.features`;
      const submitted = await interventions().submitIntervention(input);
      const reviewed = await contributions()[decision]({ ...await reviewInput(submitted.id), reviewNote: "Please correct the site description" });
      expect(reviewed.status).toBe(decision === "rejectContribution" ? "rejected" : "revision_requested");
      await expect(contributions().publishContribution(await reviewInput(submitted.id))).rejects.toMatchObject({ code: "PRECONDITION_FAILED" });
      await expect(interventions("expert").reviseIntervention({ ...input, featureId: submitted.id })).rejects.toMatchObject({ code: "NOT_FOUND" });
      expect((await tile()).length).toBe(0);
      const revised = await interventions().reviseIntervention({ ...input, featureId: submitted.id, description: "Corrected description" });
      expect(revised.status).toBe("pending_review");
      expect(revised.reviewNote).toBeNull();
      await contributions().publishContribution(await reviewInput(submitted.id));
      expect((await tile()).includes(Buffer.from(input.name))).toBe(true);
    }
  });

  it("requires explicit reviewed recovery of legacy approved rows and refuses unknown history", async () => {
    const submitted = await interventions().submitIntervention(input);
    await fixture.sql`UPDATE geo.features SET status='approved' WHERE id=${submitted.id}`;
    await expect(contributions().publishContribution(await reviewInput(submitted.id))).rejects.toMatchObject({ code: "PRECONDITION_FAILED" });
    await contributions().publishContribution({ ...await reviewInput(submitted.id), recoveryReviewNote: "Reviewed recorded consent, author and exact geometry" });
    expect((await tile()).includes(Buffer.from(input.name))).toBe(true);
    await fixture.sql`UPDATE geo.features SET status='approved', properties=properties - 'publicationConsent' WHERE id=${submitted.id}`;
    await expect(contributions().publishContribution({ ...await reviewInput(submitted.id), recoveryReviewNote: "Inspected legacy row" })).rejects.toMatchObject({ code: "PRECONDITION_FAILED" });
    for (const author of [crypto.randomUUID(), "malformed-legacy-user"]) {
      await fixture.sql`UPDATE geo.features SET properties=jsonb_set(jsonb_set(properties,'{publicationConsent}','true'::jsonb),'{submittedByUserId}',to_jsonb(${author}::text)) WHERE id=${submitted.id}`;
      await expect(contributions().publishContribution({ ...await reviewInput(submitted.id), recoveryReviewNote: "Inspected legacy author" })).rejects.toMatchObject({ code: "PRECONDITION_FAILED" });
    }
    expect((await tile()).length).toBe(0);
  });

  it("refuses a stale reviewer snapshot after the contributor changes and resubmits the boundary", async () => {
    const submitted = await interventions().submitIntervention(input);
    const snapshotA = await reviewInput(submitted.id);
    await contributions().requestRevisionContribution({ ...snapshotA, reviewNote: "Confirm the revised boundary" });
    const revisedGeometry = { type: "Polygon" as const, coordinates: [[[-105.045, 40.005], [-105.015, 40.005], [-105.015, 40.035], [-105.045, 40.035], [-105.045, 40.005]]] };
    await interventions().reviseIntervention({ ...input, featureId: submitted.id, geometry: revisedGeometry });
    await expect(contributions().publishContribution(snapshotA)).rejects.toMatchObject({ code: "CONFLICT" });
    expect((await tile()).length).toBe(0);
    const snapshotB = await reviewInput(submitted.id);
    expect(snapshotB.expectedReviewVersion).not.toBe(snapshotA.expectedReviewVersion);
    await contributions().publishContribution(snapshotB);
    expect((await tile()).includes(Buffer.from(input.name))).toBe(true);
    const [stored] = await fixture.sql`SELECT ST_AsGeoJSON(geom)::jsonb AS geometry FROM geo.features WHERE id=${submitted.id}`;
    expect(stored.geometry).toEqual(revisedGeometry);
  });

  it("reports missing layer provisioning without manufacturing a layer or submission", async () => {
    await fixture.sql`UPDATE geo.layers SET name='isolated_unprovisioned_interventions' WHERE id=${fixture.layerId}`;
    try {
      await expect(interventions().submitIntervention(input)).rejects.toMatchObject({ code: "PRECONDITION_FAILED" });
      await expect(interventions().listMySubmissions({})).rejects.toMatchObject({ code: "PRECONDITION_FAILED" });
      await expect(contributions().listPendingReview()).rejects.toMatchObject({ code: "PRECONDITION_FAILED" });
      expect(await fixture.sql`SELECT id FROM geo.features`).toHaveLength(0);
    } finally {
      await fixture.sql`UPDATE geo.layers SET name='interventions' WHERE id=${fixture.layerId}`;
    }
  });
});
