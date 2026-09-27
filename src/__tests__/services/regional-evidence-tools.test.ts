import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/server/http/bounded-upstream", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/lib/server/http/bounded-upstream")>(),
  fetchBoundedJson: vi.fn(),
}));

import {
  fetchBoundedJson,
  UpstreamHttpError,
  UpstreamPayloadError,
} from "@/lib/server/http/bounded-upstream";
import {
  callRegionalEvidenceTool,
  loadRegionalEvidenceTools,
  RegionalEvidenceArgumentError,
} from "@/lib/server/services/regional-evidence-tools";
import { APP_MAP_SURFACES } from '@/lib/server/services/regional-map-evidence';

const fetchJson = vi.mocked(fetchBoundedJson);

beforeEach(() => {
  fetchJson.mockReset();
  vi.stubEnv("AGRI_PARQUET_SERVICE_URL", "http://agri.internal:8000");
});
afterEach(() => vi.unstubAllEnvs());

describe("regional environmental tool bridge", () => {
  it("retains the map's production configuration failure", async () => {
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("AGRI_PARQUET_SERVICE_URL", "");
    await expect(loadRegionalEvidenceTools()).rejects.toThrow("AGRI_PARQUET_SERVICE_URL is not configured");
    expect(fetchJson).not.toHaveBeenCalled();
  });

  it("loads the deployed registry and per-surface value vocabulary", async () => {
    fetchJson.mockResolvedValue({
      tools: [{ type: "function", function: {
        name: "surface_value_near_point", description: "Selected-day values", parameters: { type: "object" },
      } }],
      surfaces: ["soil-field-moisture", "drought-areas"],
      feature_surfaces: [],
      value_surfaces: ["soil-field-moisture"],
    });
    const catalogue = await loadRegionalEvidenceTools();
    expect(catalogue?.tools[0].input_schema).toEqual({ type: "object" });
    expect(catalogue?.valueSurfaces).toEqual(["soil-field-moisture", ...APP_MAP_SURFACES]);
    expect(catalogue?.surfaces).toEqual(expect.arrayContaining([...APP_MAP_SURFACES]));
    expect(String(fetchJson.mock.calls[0][0])).toBe("http://agri.internal:8000/api/v1/agent-tools/");
  });

  it("preserves the selected calendar day, typed refusal and caller cancellation", async () => {
    const controller = new AbortController();
    const args = { surface_name: "soil-field-moisture", day: "2024-03-14", longitude: -116.2, latitude: 43.6 };
    const refusal = { error: "parquet_availability_withheld", note: "unknown, not zero" };
    fetchJson.mockResolvedValue({ tool: "surface_value_near_point", result: refusal });
    expect(JSON.parse(await callRegionalEvidenceTool("surface_value_near_point", args, controller.signal)))
      .toEqual(refusal);
    expect(JSON.parse(fetchJson.mock.calls[0][1].body as string)).toEqual({
      name: "surface_value_near_point", arguments: args,
    });
    expect(fetchJson.mock.calls[0][2]).toMatchObject({ timeoutMs: 15_000, signal: controller.signal });
  });

  describe("literature server_context (seam S1)", () => {
    const serverContext = {
      user_question: "my pasture is sour\nwhat can I do?",
      point: { longitude: -116.2, latitude: 43.6 },
      site_facts: { soil_ph: 5.1, days_since_fire: 120 },
    };
    const literatureArgs = { query: "sour pasture", limit: 5 };

    it("sends server_context beside the unchanged arguments for every literature tool", async () => {
      for (const tool of ["search_environmental_strategies", "get_environmental_strategies", "search_strategy_research_findings"]) {
        fetchJson.mockReset();
        fetchJson.mockResolvedValue({ tool, result: { evidence_domain: "literature_reference", result_count: 0 } });
        await callRegionalEvidenceTool(tool, literatureArgs, undefined, serverContext);
        expect(JSON.parse(fetchJson.mock.calls[0][1].body as string)).toEqual({
          name: tool, arguments: literatureArgs, server_context: serverContext,
        });
      }
    });

    it("keeps the point out of the literature arguments themselves", async () => {
      fetchJson.mockResolvedValue({ tool: "search_environmental_strategies", result: { result_count: 0 } });
      await callRegionalEvidenceTool("search_environmental_strategies", literatureArgs, undefined, serverContext);
      const body = JSON.parse(fetchJson.mock.calls[0][1].body as string);
      expect(body.arguments).not.toHaveProperty("longitude");
      expect(body.arguments).not.toHaveProperty("site_profile");
      expect(body.server_context.site_facts).not.toHaveProperty("slope_pct");
    });

    it("never sends server_context with a measured tool, even when one is passed", async () => {
      const args = { surface_name: "burn-severity", day: "2024-03-14", longitude: -116.2, latitude: 43.6 };
      fetchJson.mockResolvedValue({ tool: "surface_evidence_for_selection", result: { features: [] } });
      await callRegionalEvidenceTool("surface_evidence_for_selection", args, undefined, serverContext);
      expect(JSON.parse(fetchJson.mock.calls[0][1].body as string)).toEqual({ name: "surface_evidence_for_selection", arguments: args });
    });

    it("omits server_context from a literature call that has none", async () => {
      fetchJson.mockResolvedValue({ tool: "search_environmental_strategies", result: { result_count: 0 } });
      await callRegionalEvidenceTool("search_environmental_strategies", literatureArgs);
      expect(JSON.parse(fetchJson.mock.calls[0][1].body as string)).not.toHaveProperty("server_context");
    });

    it("retries once without server_context when a not-yet-redeployed bridge rejects the whole request (deploy-skew fallback)", async () => {
      fetchJson.mockRejectedValueOnce(new UpstreamHttpError(400, JSON.stringify({
        tool: "", error: "invalid_tool_request", code: "invalid_tool_request",
      })));
      fetchJson.mockResolvedValueOnce({ tool: "search_environmental_strategies", result: { evidence_domain: "literature_reference", result_count: 1 } });
      const result = JSON.parse(await callRegionalEvidenceTool("search_environmental_strategies", literatureArgs, undefined, serverContext));
      expect(result).toEqual({ evidence_domain: "literature_reference", result_count: 1 });
      expect(fetchJson).toHaveBeenCalledTimes(2);
      expect(JSON.parse(fetchJson.mock.calls[0][1].body as string)).toHaveProperty("server_context");
      expect(JSON.parse(fetchJson.mock.calls[1][1].body as string)).toEqual({
        name: "search_environmental_strategies", arguments: literatureArgs,
      });
    });

    it("does not retry invalid_tool_request when no server_context was sent, and surfaces it as a normal argument refusal", async () => {
      fetchJson.mockRejectedValueOnce(new UpstreamHttpError(400, JSON.stringify({
        tool: "", error: "invalid_tool_request", code: "invalid_tool_request",
      })));
      await expect(callRegionalEvidenceTool("search_environmental_strategies", literatureArgs))
        .rejects.toBeInstanceOf(RegionalEvidenceArgumentError);
      expect(fetchJson).toHaveBeenCalledTimes(1);
    });

    it("surfaces the fallback attempt's own failure when the retry without server_context also fails", async () => {
      fetchJson.mockRejectedValueOnce(new UpstreamHttpError(400, JSON.stringify({
        tool: "", error: "invalid_tool_request", code: "invalid_tool_request",
      })));
      fetchJson.mockRejectedValueOnce(new UpstreamHttpError(503));
      await expect(callRegionalEvidenceTool("search_environmental_strategies", literatureArgs, undefined, serverContext))
        .rejects.toBeInstanceOf(UpstreamHttpError);
      expect(fetchJson).toHaveBeenCalledTimes(2);
    });
  });

  it("rejects mismatched replies and preserves transport failures as failures", async () => {
    fetchJson.mockResolvedValueOnce({ tool: "wrong_tool", result: {} });
    await expect(callRegionalEvidenceTool("surface_value_near_point", {})).rejects.toBeInstanceOf(UpstreamPayloadError);
    fetchJson.mockRejectedValueOnce(new UpstreamHttpError(503));
    await expect(callRegionalEvidenceTool("surface_value_near_point", {})).rejects.toBeInstanceOf(UpstreamHttpError);
  });

  describe("400 argument-refusal detail", () => {
    it("surfaces a bounded, control-character-free detail so the caller can retry", async () => {
      fetchJson.mockRejectedValueOnce(new UpstreamHttpError(400, JSON.stringify({
        tool: "surface_value_near_point", error: "invalid_tool_arguments", code: "invalid_tool_arguments",
        detail: "site_profile.day: Extra inputs are not permitted\u0007trailing",
      })));
      const failure: unknown = await callRegionalEvidenceTool("surface_value_near_point", {}).catch((error) => error);
      expect(failure).toBeInstanceOf(RegionalEvidenceArgumentError);
      // The BEL control character is replaced with a single space, not dropped or echoed raw.
      expect((failure as Error).message).toBe("site_profile.day: Extra inputs are not permitted trailing");
    });

    it("falls back to a string `error` field when `detail` is absent", async () => {
      fetchJson.mockRejectedValueOnce(new UpstreamHttpError(400, JSON.stringify({ error: "unknown_environmental_tool" })));
      const failure: unknown = await callRegionalEvidenceTool("surface_value_near_point", {}).catch((error) => error);
      expect(failure).toBeInstanceOf(RegionalEvidenceArgumentError);
      expect((failure as Error).message).toBe("unknown_environmental_tool");
    });

    it("caps the surfaced detail at 600 characters", async () => {
      fetchJson.mockRejectedValueOnce(new UpstreamHttpError(400, JSON.stringify({ detail: "x".repeat(1_000) })));
      const failure: unknown = await callRegionalEvidenceTool("surface_value_near_point", {}).catch((error) => error);
      expect(failure).toBeInstanceOf(RegionalEvidenceArgumentError);
      expect((failure as Error).message).toHaveLength(600);
    });

    it("keeps a 500 failure generic even with a detail-shaped JSON body", async () => {
      fetchJson.mockRejectedValueOnce(new UpstreamHttpError(500, JSON.stringify({ detail: "should never surface" })));
      const failure: unknown = await callRegionalEvidenceTool("surface_value_near_point", {}).catch((error) => error);
      expect(failure).toBeInstanceOf(UpstreamHttpError);
      expect(failure).not.toBeInstanceOf(RegionalEvidenceArgumentError);
    });

    it("keeps an oversized 400 body (no captured bodyText) generic", async () => {
      fetchJson.mockRejectedValueOnce(new UpstreamHttpError(400));
      const failure: unknown = await callRegionalEvidenceTool("surface_value_near_point", {}).catch((error) => error);
      expect(failure).toBeInstanceOf(UpstreamHttpError);
      expect(failure).not.toBeInstanceOf(RegionalEvidenceArgumentError);
    });

    it("keeps a non-JSON 400 body generic", async () => {
      fetchJson.mockRejectedValueOnce(new UpstreamHttpError(400, "<html>not json</html>"));
      const failure: unknown = await callRegionalEvidenceTool("surface_value_near_point", {}).catch((error) => error);
      expect(failure).toBeInstanceOf(UpstreamHttpError);
      expect(failure).not.toBeInstanceOf(RegionalEvidenceArgumentError);
    });
  });
});
