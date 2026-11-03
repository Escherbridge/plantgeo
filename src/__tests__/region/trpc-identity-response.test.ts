// @vitest-environment node
import { afterEach, expect, it, vi } from "vitest";
import fixture from "../../../services/agri-data-service/src/agri_data_service/foundation/region/custom_colorado.fixture.json";
vi.mock("@trpc/server/adapters/fetch", () => ({ fetchRequestHandler: vi.fn() }));
vi.mock("@/lib/server/trpc/router", () => ({ appRouter: {} }));
vi.mock("@/lib/server/trpc/init", () => ({ createTRPCContext: vi.fn() }));
import { fetchRequestHandler } from "@trpc/server/adapters/fetch";
import { POST, GET } from "@/app/api/trpc/[trpc]/route";
import { getRegion } from "@/lib/region/region";
import { definitionDigest, EXPECTED_REGION_HEADER, SERVED_REGION_HEADER } from "@/lib/region/identity";
import { MAX_INGRESS_BODY_BYTES } from "@/lib/server/security/ingress";

afterEach(() => { vi.unstubAllEnvs(); vi.resetAllMocks(); });
function select(west = -108) {
  vi.stubEnv("NEXT_PUBLIC_PLANTGEO_REGION", fixture.slug);
  vi.stubEnv("NEXT_PUBLIC_PLANTGEO_REGION_MANIFEST_JSON", JSON.stringify({ ...fixture, envelope: { ...fixture.envelope, west } }));
}
it("preserves the custom request-size refusal and serves its identity header", async () => {
  select();
  const definition = definitionDigest(getRegion());
  const response = await POST(new Request("https://example.test/api/trpc/test", {
    method: "POST", body: "{}", headers: {
      [EXPECTED_REGION_HEADER]: definition, "content-length": String(MAX_INGRESS_BODY_BYTES + 1),
    },
  }));
  expect(response.status).toBe(413);
  expect(response.headers.get(SERVED_REGION_HEADER)).toBe(definition);
  expect(fetchRequestHandler).not.toHaveBeenCalled();
});
it("stamps the actual new definition on an in-flight configuration refusal", async () => {
  select();
  const old = definitionDigest(getRegion());
  vi.mocked(fetchRequestHandler).mockImplementationOnce(async () => {
    select(-109);
    return Response.json({ result: "old" });
  });
  const response = await GET(new Request("https://example.test/api/trpc/test", {
    headers: { [EXPECTED_REGION_HEADER]: old },
  }));
  expect(response.status).toBe(409);
  expect(response.headers.get(SERVED_REGION_HEADER)).toBe(definitionDigest(getRegion()));
  expect(response.headers.get(SERVED_REGION_HEADER)).not.toBe(old);
});
