// @vitest-environment node
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import fixture from "../../../services/agri-data-service/src/agri_data_service/foundation/region/custom_colorado.fixture.json";
import { callRegionalEvidenceTool, loadRegionalEvidenceTools, RegionalEvidenceArgumentError } from "@/lib/server/services/regional-evidence-tools";
import { definitionDigest, EXPECTED_REGION_HEADER, SERVED_REGION_HEADER } from "@/lib/region/identity";
import { getRegion } from "@/lib/region/region";
import { UpstreamHttpError } from "@/lib/server/http/bounded-upstream";

const tool = "surface_value_near_point";
const args = { surface_name: "soil-field-moisture", day: "2024-03-14", longitude: -116.2, latitude: 43.6 };
const catalogue = { tools: [], surfaces: [], feature_surfaces: [], value_surfaces: [] };
beforeEach(() => {
  vi.stubEnv("AGRI_PARQUET_SERVICE_URL", "https://agri.example.test");
  vi.stubEnv("NEXT_PUBLIC_PLANTGEO_REGION", fixture.slug);
  vi.stubEnv("NEXT_PUBLIC_PLANTGEO_REGION_MANIFEST_JSON", JSON.stringify(fixture));
});
afterEach(() => { vi.unstubAllEnvs(); vi.unstubAllGlobals(); });

it("checks identity for both the catalogue and the selected-day call", async () => {
  const expected = definitionDigest(getRegion());
  const headers = { [SERVED_REGION_HEADER]: expected };
  const fetch = vi.fn().mockResolvedValueOnce(Response.json(catalogue, { headers }))
    .mockResolvedValueOnce(Response.json({ tool, result: { requested_day: args.day } }, { headers }));
  vi.stubGlobal("fetch", fetch);
  await expect(loadRegionalEvidenceTools()).resolves.not.toBeNull();
  await expect(callRegionalEvidenceTool(tool, args)).resolves.toBe(JSON.stringify({ requested_day: args.day }));
  for (const call of fetch.mock.calls) expect(new Headers(call[1].headers).get(EXPECTED_REGION_HEADER)).toBe(expected);
  expect(JSON.parse(fetch.mock.calls[1][1].body)).toEqual({ name: tool, arguments: args });
});

it.each([null, "different-definition"])("refuses custom catalogue and tool response identity %s", async (identity) => {
  const headers: HeadersInit = identity === null ? {} : { [SERVED_REGION_HEADER]: identity };
  vi.stubGlobal("fetch", vi.fn().mockImplementation(async () => Response.json(catalogue, { headers })));
  await expect(loadRegionalEvidenceTools()).rejects.toThrow("region_identity_mismatch");
  await expect(callRegionalEvidenceTool(tool, args)).rejects.toThrow("region_identity_mismatch");
});

it("retains packaged legacy response compatibility", async () => {
  vi.stubEnv("NEXT_PUBLIC_PLANTGEO_REGION", "pnw");
  vi.stubEnv("NEXT_PUBLIC_PLANTGEO_REGION_MANIFEST_JSON", "");
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json(catalogue)));
  await expect(loadRegionalEvidenceTools()).resolves.not.toBeNull();
});

it.each(["region_namespace_receipt_missing", "region_source_support_not_admitted", "region_admission_refused"])(
  "keeps %s a 409 transport refusal rather than a model argument error", async (code) => {
    const fetch = vi.fn().mockImplementation(async () => Response.json({ code, error: code }, { status: 409 }));
    vi.stubGlobal("fetch", fetch);
    const error = await callRegionalEvidenceTool(tool, args).catch((failure: unknown) => failure);
    expect(error).toBeInstanceOf(UpstreamHttpError);
    expect(error).not.toBeInstanceOf(RegionalEvidenceArgumentError);
    expect(error).toMatchObject({ status: 409 });
    expect(fetch).toHaveBeenCalledOnce();
  }
);
