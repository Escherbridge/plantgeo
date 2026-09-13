import { z } from "zod";
import {
  fetchBoundedJson,
  providerUrl,
  UpstreamPayloadError,
} from "@/lib/server/http/bounded-upstream";

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
  return {
    tools: parsed.data.tools.map(({ function: tool }) => ({
      name: tool.name,
      description: tool.description,
      input_schema: tool.parameters,
    })),
    surfaces: parsed.data.surfaces,
    featureSurfaces: parsed.data.feature_surfaces,
    valueSurfaces: parsed.data.value_surfaces,
  };
}

/** Preserve tool refusals and named calendar days while bounding transport and cancellation. */
export async function callRegionalEvidenceTool(
  name: string,
  args: Record<string, unknown>,
  signal?: AbortSignal,
): Promise<string> {
  const body = JSON.stringify({ name, arguments: args });
  if (new TextEncoder().encode(body).byteLength > 32 * 1024) {
    throw new UpstreamPayloadError("Environmental tool request exceeded the byte limit");
  }
  const wire = await fetchBoundedJson(endpoint("call"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body,
  }, { maxBytes: 2 * 1024 * 1024 + 1024, timeoutMs: 15_000, signal });
  const parsed = resultSchema.safeParse(wire);
  if (!parsed.success || parsed.data.tool !== name) {
    throw new UpstreamPayloadError("Environmental tool response did not match the requested tool");
  }
  return JSON.stringify(parsed.data.result);
}
