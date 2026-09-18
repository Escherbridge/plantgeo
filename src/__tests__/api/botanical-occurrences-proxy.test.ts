import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Ingress validation and the fail-closed pointer paths of `GET /api/botanical-occurrences`.
 *
 * MOCKED AT THE NETWORK BOUNDARY, exactly as `botanical-occurrences-client.test.ts` is: only
 * `providerUrl` and `fetchBoundedJson` are stubbed, so the route's zod ingress schema, the bbox
 * ceiling, the server client's zod decoding, the §4a pointer decode and the error taxonomy all run
 * for real. NO live API call is made, and none of these cases would pass if the real plane were
 * reachable -- they assert what happens when it answers a specific way.
 */
vi.mock("@/lib/server/http/bounded-upstream", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/server/http/bounded-upstream")>();
  return {
    ...actual,
    providerUrl: vi.fn(actual.providerUrl),
    fetchBoundedJson: vi.fn(),
  };
});

import { NextRequest } from "next/server";
import { fetchBoundedJson, providerUrl, UpstreamHttpError, UpstreamTimeoutError } from "@/lib/server/http/bounded-upstream";
import { GET } from "@/app/api/botanical-occurrences/route";
import { botanicalProxyAnswerSchema } from "@/lib/environmental/botanical-proxy-contract";

const mockedProviderUrl = vi.mocked(providerUrl);
const mockedFetch = vi.mocked(fetchBoundedJson);

const wirePointer = {
  product: "botanical-occurrences",
  state: "current",
  release_set_id: "ubc-v16.43",
  generation_id: "ubc-v16.43",
  manifest_sha256: "b".repeat(64),
  manifest_key: "botanical-occurrences/ubc-v16.43/manifest.json",
  pointer_kind: "latest_v1",
  pointer_schema_version: 1,
  pointer_written_at: "2026-09-10T00:00:00Z",
};

const wireDetail = {
  product: "botanical-occurrences",
  state: "detail",
  release_set_id: "ubc-v16.43",
  published_at: "2026-09-10T00:00:00Z",
  taxonomy_recipe_version: "source-names-v1",
  qc_policy_version: "qc-v1",
  support_id: null,
  features: [],
  truncated: false,
  next_cursor: null,
  counts: { returned: 0, matched: 0, withheld: 3, nonspatial: 3, excluded_by_qc: 3 },
};

/** The route reads only `nextUrl`; a bare NextRequest over a full URL is the whole fixture. */
function requestFor(query: string): NextRequest {
  return new NextRequest(`http://plantgeo.test/api/botanical-occurrences?${query}`);
}

beforeEach(() => {
  mockedProviderUrl.mockReset();
  mockedFetch.mockReset();
  mockedProviderUrl.mockImplementation(() => new URL("http://agri.internal:8000"));
});

afterEach(() => vi.restoreAllMocks());

describe("ingress validation", () => {
  it("refuses a bbox that is not four numbers, without calling upstream", async () => {
    const response = await GET(requestFor("bbox=-124,48,-122&zoom=13"));

    expect(response.status).toBe(400);
    await expect(response.json()).resolves.toMatchObject({ reason: "invalid_request" });
    expect(mockedFetch).not.toHaveBeenCalled();
  });

  it("refuses a bbox outside WGS84 bounds", async () => {
    const response = await GET(requestFor("bbox=-200,48,-122,50&zoom=13"));

    expect(response.status).toBe(400);
    expect(mockedFetch).not.toHaveBeenCalled();
  });

  it("refuses a bbox whose min ordinates are not below its max", async () => {
    const response = await GET(requestFor("bbox=-122,50,-124,48&zoom=13"));

    expect(response.status).toBe(400);
    expect(mockedFetch).not.toHaveBeenCalled();
  });

  it("refuses a fractional zoom rather than truncating it into another answer band", async () => {
    const response = await GET(requestFor("bbox=-124,48,-122,50&zoom=12.5"));

    expect(response.status).toBe(400);
    expect(mockedFetch).not.toHaveBeenCalled();
  });

  it("refuses a limit above the plane's published ceiling", async () => {
    const response = await GET(requestFor("bbox=-124,48,-122,50&zoom=13&limit=5000"));

    expect(response.status).toBe(400);
    expect(mockedFetch).not.toHaveBeenCalled();
  });

  /**
   * The ingress bound must MATCH the plane's, not merely exist: 4 square degrees at detail zoom,
   * 100 at the 0.05 rung. A viewport 6 degrees wide is refused at zoom 13 and accepted at zoom 8.
   */
  it("refuses a bbox wider than the detail band's ceiling before spending a round trip", async () => {
    const response = await GET(requestFor("bbox=-126,45,-120,51&zoom=13"));

    expect(response.status).toBe(400);
    await expect(response.json()).resolves.toMatchObject({ reason: "bbox_too_large_for_zoom" });
    expect(mockedFetch).not.toHaveBeenCalled();
  });

  it("accepts that same bbox at an aggregate zoom, where the 0.05 rung's ceiling is 100", async () => {
    mockedFetch.mockResolvedValueOnce(wirePointer);
    mockedFetch.mockResolvedValueOnce({
      ...wireDetail,
      state: "aggregate",
      support_id: "grid-0.05",
      cells: [],
      counts: { returned: 0, matched: 0 },
    });

    const response = await GET(requestFor("bbox=-126,45,-120,51&zoom=8"));

    expect(response.status).toBe(200);
  });
});

describe("pointer resolution", () => {
  it("pins the query to the generation the checksum-bound pointer names", async () => {
    mockedFetch.mockResolvedValueOnce(wirePointer);
    mockedFetch.mockResolvedValueOnce(wireDetail);

    const response = await GET(requestFor("bbox=-124,48,-122,50&zoom=13"));
    const body = await response.json();

    expect(response.status).toBe(200);
    expect(botanicalProxyAnswerSchema.safeParse(body).success).toBe(true);
    expect(body.pointer).toMatchObject({
      generationId: "ubc-v16.43",
      manifestChecksum: wirePointer.manifest_sha256,
    });
    const queryUrl = mockedFetch.mock.calls[1][0] as URL;
    expect(queryUrl.searchParams.get("release_set_id")).toBe("ubc-v16.43");
  });

  /**
   * The four §4a fail-closed conditions must each arrive as a 503 carrying its OWN reason. An empty
   * collection here would report an integrity failure as a lane with nothing in it.
   */
  it.each(["pointer_missing", "pointer_malformed", "pointer_stale", "pointer_checksum_invalid"])(
    "fails closed with %s rather than answering an empty collection",
    async (reason) => {
      mockedFetch.mockResolvedValueOnce({
        product: "botanical-occurrences",
        state: "unavailable",
        reason,
        detail: "detail text",
        note: "note text",
      });

      const response = await GET(requestFor("bbox=-124,48,-122,50&zoom=13"));
      const body = await response.json();

      expect(response.status).toBe(503);
      expect(body.reason).toBe(reason);
      expect(body).not.toHaveProperty("features");
      // The pointer failed, so the query was never asked.
      expect(mockedFetch).toHaveBeenCalledTimes(1);
    }
  );

  it("passes a bridged answer's legacy pointer kind through to the browser", async () => {
    mockedFetch.mockResolvedValueOnce({
      ...wirePointer,
      pointer_kind: "legacy_current_json",
      pointer_written_at: null,
    });
    mockedFetch.mockResolvedValueOnce(wireDetail);

    const response = await GET(requestFor("bbox=-124,48,-122,50&zoom=13"));
    const body = await response.json();

    expect(response.status).toBe(200);
    expect(body.pointer).toMatchObject({ pointerKind: "legacy_current_json", pointerWrittenAt: null });
  });

  it("reports a pointer body it cannot parse as a contract mismatch, not an absence", async () => {
    mockedFetch.mockResolvedValueOnce({ product: "botanical-occurrences", state: "current", release_set_id: "x" });

    const response = await GET(requestFor("bbox=-124,48,-122,50&zoom=13"));

    expect(response.status).toBe(502);
    await expect(response.json()).resolves.toMatchObject({ reason: "contract_mismatch" });
  });

  it("refuses a pointer whose checksum is not a sha256", async () => {
    mockedFetch.mockResolvedValueOnce({ ...wirePointer, manifest_sha256: "0xdeadbeef" });

    const response = await GET(requestFor("bbox=-124,48,-122,50&zoom=13"));

    expect(response.status).toBe(502);
  });
});

describe("upstream faults", () => {
  it("answers 504 on an upstream timeout", async () => {
    mockedFetch.mockRejectedValueOnce(new UpstreamTimeoutError("Upstream request timed out"));

    const response = await GET(requestFor("bbox=-124,48,-122,50&zoom=13"));

    expect(response.status).toBe(504);
    await expect(response.json()).resolves.toMatchObject({ reason: "upstream_timeout" });
  });

  it("passes a 4xx from the plane through as the caller mistake it is", async () => {
    mockedFetch.mockResolvedValueOnce(wirePointer);
    mockedFetch.mockRejectedValueOnce(new UpstreamHttpError(409));

    const response = await GET(requestFor("bbox=-124,48,-122,50&zoom=13"));

    expect(response.status).toBe(409);
    await expect(response.json()).resolves.toMatchObject({ reason: "plane_rejected" });
  });

  it("collapses a 5xx from the plane to 502", async () => {
    mockedFetch.mockResolvedValueOnce(wirePointer);
    mockedFetch.mockRejectedValueOnce(new UpstreamHttpError(500));

    const response = await GET(requestFor("bbox=-124,48,-122,50&zoom=13"));

    expect(response.status).toBe(502);
  });
});
