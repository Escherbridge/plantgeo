// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";
import fixture from "../../../services/agri-data-service/src/agri_data_service/foundation/region/custom_colorado.fixture.json";
import { fetchRegionJson } from "@/lib/server/http/region-identity";
import { withRegionIdentity } from "@/lib/net/region-identity-fetch";
import { definitionDigest, EXPECTED_REGION_HEADER, SERVED_REGION_HEADER } from "@/lib/region/identity";
import { getRegion } from "@/lib/region/region";
import { assertServedRegionMatchesBundle, resetParquetCoverageCacheForTests } from "@/lib/server/services/parquet-plane-client";

afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); resetParquetCoverageCacheForTests(); });
function custom() {
  vi.stubEnv("NEXT_PUBLIC_PLANTGEO_REGION", fixture.slug);
  vi.stubEnv("NEXT_PUBLIC_PLANTGEO_REGION_MANIFEST_JSON", JSON.stringify(fixture));
}
const options = { maxBytes: 1024, timeoutMs: 1000 };
describe("identity on actual bounded row exchanges", () => {
  it.each([null, "wrong-definition"])("cancels an unread rejected browser stream: %s", async (served) => {
    custom();
    const cancel = vi.fn();
    const body = new ReadableStream({ cancel });
    const response = new Response(body, { headers: served === null ? {} : { [SERVED_REGION_HEADER]: served } });
    await expect(withRegionIdentity(vi.fn().mockResolvedValue(response))("https://example.test/api/trpc"))
      .rejects.toThrow("region_identity_mismatch");
    expect(cancel).toHaveBeenCalledOnce();
  });
  it("does not let a cancellation failure mask identity rejection", async () => {
    custom();
    const response = new Response(new ReadableStream({ cancel: () => Promise.reject(new Error("cancel failed")) }));
    await expect(withRegionIdentity(vi.fn().mockResolvedValue(response))("https://example.test/api/trpc"))
      .rejects.toThrow("region_identity_mismatch");
  });
  it("leaves a matching browser response stream available to its reader", async () => {
    custom();
    const cancel = vi.fn();
    const response = new Response(new ReadableStream({ cancel }), {
      headers: { [SERVED_REGION_HEADER]: definitionDigest(getRegion()) },
    });
    await expect(withRegionIdentity(vi.fn().mockResolvedValue(response))("https://example.test/api/trpc")).resolves.toBe(response);
    expect(cancel).not.toHaveBeenCalled();
    await response.body?.cancel();
  });
  it("refuses an old browser bundle's response from a newer web definition", async () => {
    custom();
    const fetcher = vi.fn().mockResolvedValue(Response.json({ result: "new server" }, {
      headers: { [SERVED_REGION_HEADER]: "new-server-definition" },
    }));
    await expect(withRegionIdentity(fetcher)("https://example.test/api/trpc")).rejects.toThrow("region_identity_mismatch");
    expect(new Headers(fetcher.mock.calls[0][1].headers).get(EXPECTED_REGION_HEADER)).toBe(definitionDigest(getRegion()));
  });
  it("allows a packaged browser to receive legacy web silence", async () => {
    vi.stubEnv("NEXT_PUBLIC_PLANTGEO_REGION", "pnw");
    vi.stubEnv("NEXT_PUBLIC_PLANTGEO_REGION_MANIFEST_JSON", "");
    await expect(withRegionIdentity(vi.fn().mockResolvedValue(Response.json({ result: [] })))("https://example.test/api/trpc"))
      .resolves.toBeInstanceOf(Response);
  });
  it("sends expected identity and accepts the matching response", async () => {
    custom();
    const expected = definitionDigest(getRegion());
    const fetch = vi.fn().mockResolvedValue(Response.json({ rows: [] }, { headers: { [SERVED_REGION_HEADER]: expected } }));
    vi.stubGlobal("fetch", fetch);
    await expect(fetchRegionJson("https://example.test/rows", {}, options)).resolves.toEqual({ rows: [] });
    expect(new Headers(fetch.mock.calls[0][1].headers).get(EXPECTED_REGION_HEADER)).toBe(expected);
  });
  it.each([null, "wrong-definition"])("refuses missing or contradictory custom response identity %s", async (served) => {
    custom();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ rows: ["foreign"] }, {
      headers: served === null ? {} : { [SERVED_REGION_HEADER]: served },
    })));
    await expect(fetchRegionJson("https://example.test/rows", {}, options)).rejects.toThrow("region_identity_mismatch");
  });
  it("permits packaged legacy silence but refuses a stated mismatch", async () => {
    vi.stubEnv("NEXT_PUBLIC_PLANTGEO_REGION", "pnw");
    vi.stubEnv("NEXT_PUBLIC_PLANTGEO_REGION_MANIFEST_JSON", "");
    const fetch = vi.fn().mockResolvedValueOnce(Response.json({ rows: [] }))
      .mockResolvedValueOnce(Response.json({ rows: [] }, { headers: { [SERVED_REGION_HEADER]: "wrong" } }));
    vi.stubGlobal("fetch", fetch);
    await expect(fetchRegionJson("https://example.test/rows", {}, options)).resolves.toEqual({ rows: [] });
    await expect(fetchRegionJson("https://example.test/rows", {}, options)).rejects.toThrow("region_identity_mismatch");
  });
  it("refuses a custom cold census failure", async () => {
    custom();
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    await expect(assertServedRegionMatchesBundle()).rejects.toThrow();
  });
});
