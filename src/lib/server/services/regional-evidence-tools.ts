import { z } from "zod";
import {
  fetchBoundedJson,
  providerUrl,
  UpstreamHttpError,
  UpstreamPayloadError,
} from "@/lib/server/http/bounded-upstream";
import { callLandContextTool, isLandContextTool, landContextTools } from "@/lib/server/services/land-context-tools";
import { isStrategyKnowledgeTool } from "@/lib/regional-intelligence";
import { APP_MAP_SURFACES, isAppMapSurface, readBoundedAppMapEvidence } from "./regional-map-evidence";
import type { FactProvenance } from "./site-brief";

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

/** Server-read site values for the literature tools (S1 `site_facts`, C3); each carries a basis in `site_facts_provenance`. */
export interface LiteratureSiteFacts {
  soil_ph?: number;
  soil_organic_carbon_pct?: number;
  sand_pct?: number;
  clay_pct?: number;
  electrical_conductivity_ds_m?: number;
  burn_severity?: "low" | "moderate" | "high";
  days_since_fire?: number;
  annual_precip_mm?: number;
  land_cover?: string;
}

/** Server-owned literature context (seam S1, C3 delta); out of band from the model's arguments. See services/AGENTS.md §literature-server-context. */
export interface LiteratureServerContext {
  user_question?: string;
  point?: { longitude: number; latitude: number };
  site_facts?: LiteratureSiteFacts;
  /** C3: one entry per `site_facts` key whose basis the server knows; agri drops keys without one. */
  site_facts_provenance?: Partial<Record<keyof LiteratureSiteFacts, FactProvenance>>;
  /** C3/C4: the site brief's literature seed, used as `context_query` when no question was typed. */
  site_brief_query?: string;
}

/** The agri SoilGrids point tool (CONTRACT C6); see soil/AGENTS.md §soil-tool. */
export const SOIL_PROPERTIES_TOOL_NAME = "soil_properties_at_point";

/** Appended to the served description so the label rule travels with the tool, whatever agri says. */
const SOIL_PROPERTIES_TOOL_LABEL_RULE = " Every value it returns is a SoilGrids v2.0 250 m model estimate: repeat its basis label and depth "
  + "whenever you cite one, and never call it a measurement or an observation.";

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

/**
 * The whole-request refusal code the bridge returns when a top-level field is unrecognised
 * (`AgentToolCallRequest(extra="forbid")`) -- distinct from `invalid_tool_arguments`, which names a
 * broken argument the model can fix. During the wave-2 deploy window agri may still be running the
 * pre-`server_context` schema; see services/AGENTS.md §literature-server-context.
 */
const INVALID_TOOL_REQUEST_CODE = "invalid_tool_request";

function isInvalidToolRequestRefusal(error: UpstreamHttpError): boolean {
  if (error.status !== 400 || !error.bodyText) return false;
  let body: unknown;
  try {
    body = JSON.parse(error.bodyText);
  } catch {
    return false;
  }
  if (typeof body !== "object" || body === null) return false;
  const record = body as Record<string, unknown>;
  return record.code === INVALID_TOOL_REQUEST_CODE || record.error === INVALID_TOOL_REQUEST_CODE;
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
    description: tool.name === SOIL_PROPERTIES_TOOL_NAME && !tool.description.includes(SOIL_PROPERTIES_TOOL_LABEL_RULE.trim())
      ? `${tool.description}${SOIL_PROPERTIES_TOOL_LABEL_RULE}` : tool.description,
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
  serverContext?: LiteratureServerContext,
): Promise<string> {
  // Land-context tools run in-process against this app's own Postgres-backed
  // reader service (see land-context-tools.ts) and never touch the agri
  // Parquet bridge below, so they work even when AGRI_PARQUET_SERVICE_URL is
  // unconfigured and carry no network transport bound.
  if (isLandContextTool(name)) return callLandContextTool(name, args);
  if (name === "surface_evidence_for_selection" && isAppMapSurface(args.surface_name)) {
    return JSON.stringify(await readBoundedAppMapEvidence(args, signal));
  }

  const post = async (context: LiteratureServerContext | undefined): Promise<unknown> => {
    // `server_context` is honoured only by the three literature tools, so it is never sent with any other.
    const body = JSON.stringify({
      name, arguments: args,
      ...(context && isStrategyKnowledgeTool(name) ? { server_context: context } : {}),
    });
    if (new TextEncoder().encode(body).byteLength > 32 * 1024) {
      throw new UpstreamPayloadError("Environmental tool request exceeded the byte limit");
    }
    return fetchBoundedJson(endpoint("call"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
    }, { maxBytes: 2 * 1024 * 1024 + 1024, timeoutMs: 15_000, signal });
  };
  let wire: unknown;
  try {
    wire = await post(serverContext);
  } catch (error) {
    if (error instanceof UpstreamHttpError && serverContext && isInvalidToolRequestRefusal(error)) {
      // Deploy-skew fallback (wave-2 fix-stage review): a not-yet-redeployed agri bridge rejects the
      // WHOLE request over the unrecognised `server_context` field. Retry once without it so the
      // report degrades to `caller_asserted` grounding instead of losing literature entirely for the
      // life of the skew, and so this never spends the caller's rejected-call budget (ai-prompt.ts).
      try {
        wire = await post(undefined);
      } catch (fallbackError) {
        if (fallbackError instanceof UpstreamHttpError) {
          const detail = argumentErrorDetail(fallbackError);
          if (detail) throw new RegionalEvidenceArgumentError(detail);
        }
        throw fallbackError;
      }
    } else {
      if (error instanceof UpstreamHttpError) {
        const detail = argumentErrorDetail(error);
        if (detail) throw new RegionalEvidenceArgumentError(detail);
      }
      throw error;
    }
  }
  const parsed = resultSchema.safeParse(wire);
  if (!parsed.success || parsed.data.tool !== name) {
    throw new UpstreamPayloadError("Environmental tool response did not match the requested tool");
  }
  return JSON.stringify(parsed.data.result);
}
