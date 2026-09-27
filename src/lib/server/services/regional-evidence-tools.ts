import { z } from "zod";
import {
  fetchBoundedJson,
  providerUrl,
  UpstreamHttpError,
  UpstreamPayloadError,
} from "@/lib/server/http/bounded-upstream";
import { callLandContextTool, isLandContextTool, landContextTools } from "@/lib/server/services/land-context-tools";
import { APP_MAP_SURFACES, isAppMapSurface, readBoundedAppMapEvidence } from "./regional-map-evidence";

export interface RegionalEvidenceTool {
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
}

export interface RegionalEvidenceCatalogue {
  tools: RegionalEvidenceTool[];
  surfaces: string[];
  featureSurfaces: string[];
  valueSurfaces: string[];
}

const catalogueSchema = z.object({
  tools: z.array(z.object({
    type: z.literal("function"),
    function: z.object({
      name: z.string().min(1).max(80),
      description: z.string(),
      parameters: z.record(z.string(), z.unknown()),
    }),
  })).max(32),
  surfaces: z.array(z.string()).max(64),
  feature_surfaces: z.array(z.string()).max(64),
  value_surfaces: z.array(z.string()).max(64),
});

const resultSchema = z.object({ tool: z.string(), result: z.record(z.string(), z.unknown()) });

/**
 * The bridge rejected this call's *arguments* (HTTP 400) and returned a bounded, value-free
 * explanation of which field broke which rule -- unlike a transport/5xx failure, this is
 * self-correctable, so the caller can hand it back to the model as a fixable refusal instead of
 * a generic read failure. See services/agri-data-service/.../agent/llm.py
 * `argument_error_detail`/`tool_error_payload` and routes/agent_tools.py `_refusal`.
 */
export class RegionalEvidenceArgumentError extends Error {}

const MAX_ARGUMENT_ERROR_DETAIL_CHARACTERS = 600;

/**
 * Pull a renderable explanation out of a 400 refusal body, or null when the body is missing,
 * oversized, not JSON, or doesn't carry a string `detail`/`error` -- those stay a generic
 * `UpstreamHttpError` so the caller's existing transport-failure handling still applies.
 */
function argumentErrorDetail(error: UpstreamHttpError): string | null {
  if (error.status !== 400 || !error.bodyText) return null;
  let body: unknown;
  try {
    body = JSON.parse(error.bodyText);
  } catch {
    return null;
  }
  if (typeof body !== "object" || body === null) return null;
  const record = body as Record<string, unknown>;
  const raw = typeof record.detail === "string" ? record.detail
    : typeof record.error === "string" ? record.error
      : null;
  if (!raw) return null;
   
  const stripped = raw.replace(/[\x00-\x1F\x7F]/g, " ").trim();
  return stripped.length > 0 ? stripped.slice(0, MAX_ARGUMENT_ERROR_DETAIL_CHARACTERS) : null;
}

function endpoint(path: string): URL {
  const url = providerUrl("AGRI_PARQUET_SERVICE_URL", "http://localhost:8000");
  url.pathname = `${url.pathname.replace(/\/$/, "")}/api/v1/agent-tools/${path}`;
  return url;
}

/** Load the deployed tool registry; see services/AGENTS.md for the internal bridge contract. */
export async function loadRegionalEvidenceTools(
  signal?: AbortSignal,
): Promise<RegionalEvidenceCatalogue | null> {
  const wire = await fetchBoundedJson(endpoint(""), {}, {
    maxBytes: 256 * 1024,
    timeoutMs: 8_000,
    signal,
  });
  const parsed = catalogueSchema.safeParse(wire);
  if (!parsed.success) throw new UpstreamPayloadError("Invalid environmental tool catalogue");
  const names = parsed.data.tools.map((tool) => tool.function.name);
  if (new Set(names).size !== names.length || names.includes("species_information")) {
    throw new UpstreamPayloadError("Invalid environmental tool registry");
  }
  const remoteTools = parsed.data.tools.map(({ function: tool }) => ({
    name: tool.name,
    description: tool.description,
    input_schema: tool.parameters,
  }));
  // Land-context tools are registered in-process (see land-context-tools.ts)
  // rather than served by the deployed agri Parquet bridge, but they share
  // this same catalogue so `ai-prompt.ts` dispatches both uniformly. Guard
  // against a name collision with the remote registry rather than silently
  // shadowing one tool with the other.
  //
  // Built HERE, per catalogue load, so the descriptions and `state` enums the model is shown are
  // the selected region's; they stay registered in every region and refuse where the layer is
  // unbound (STYLE-REVIEW-W8 B1).
  const inProcessLandContextTools = landContextTools().map(({ name, description, input_schema }) => ({
    name,
    description,
    input_schema,
  }));
  if (remoteTools.some((tool) => isLandContextTool(tool.name))) {
    throw new UpstreamPayloadError("Environmental tool registry collides with a land-context tool name");
  }
  return {
    tools: [...remoteTools, ...inProcessLandContextTools],
    surfaces: [...new Set([...parsed.data.surfaces, ...APP_MAP_SURFACES])],
    featureSurfaces: parsed.data.feature_surfaces,
    valueSurfaces: [...new Set([...parsed.data.value_surfaces, ...APP_MAP_SURFACES])],
  };
}

/** Preserve tool refusals and named calendar days while bounding transport and cancellation. */
export async function callRegionalEvidenceTool(
  name: string,
  args: Record<string, unknown>,
  signal?: AbortSignal,
): Promise<string> {
  // Land-context tools run in-process against this app's own Postgres-backed
  // reader service (see land-context-tools.ts) and never touch the agri
  // Parquet bridge below, so they work even when AGRI_PARQUET_SERVICE_URL is
  // unconfigured and carry no network transport bound.
  if (isLandContextTool(name)) return callLandContextTool(name, args);
  if (name === "surface_evidence_for_selection" && isAppMapSurface(args.surface_name)) {
    return JSON.stringify(await readBoundedAppMapEvidence(args, signal));
  }

  const body = JSON.stringify({ name, arguments: args });
  if (new TextEncoder().encode(body).byteLength > 32 * 1024) {
    throw new UpstreamPayloadError("Environmental tool request exceeded the byte limit");
  }
  let wire: unknown;
  try {
    wire = await fetchBoundedJson(endpoint("call"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
    }, { maxBytes: 2 * 1024 * 1024 + 1024, timeoutMs: 15_000, signal });
  } catch (error) {
    if (error instanceof UpstreamHttpError) {
      const detail = argumentErrorDetail(error);
      if (detail) throw new RegionalEvidenceArgumentError(detail);
    }
    throw error;
  }
  const parsed = resultSchema.safeParse(wire);
  if (!parsed.success || parsed.data.tool !== name) {
    throw new UpstreamPayloadError("Environmental tool response did not match the requested tool");
  }
  return JSON.stringify(parsed.data.result);
}
