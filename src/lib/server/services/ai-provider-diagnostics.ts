import { remediationReportSchema } from "./remediation-report";

const MAX_RAW_ERROR_BYTES = 16_384;
const KNOWN_CODES = new Set(["INVALID_ARGUMENT", "RESOURCE_EXHAUSTED", "FAILED_PRECONDITION", "PERMISSION_DENIED", "UNAUTHENTICATED", "NOT_FOUND", "UNAVAILABLE", "INTERNAL", "DEADLINE_EXCEEDED", "invalid_request_error", "invalid_argument", "context_length_exceeded", "rate_limit_exceeded", "rate_limit_error", "authentication_error", "permission_error", "not_found_error", "api_error", "server_error", "insufficient_quota"]);
const KNOWN_PROVIDERS = new Set(["Google", "Google AI Studio", "Google Vertex", "Google Vertex AI", "OpenAI", "Anthropic", "Amazon Bedrock", "Azure", "Together", "Fireworks", "DeepInfra"]);
const KNOWN_PARAMS = new Set(["messages", "tools", "tool_choice", "max_tokens", "model", "response_format", "temperature", "stream"]);

function field(value: unknown, key: string): unknown {
  if (typeof value !== "object" || value === null) return undefined;
  return Object.getOwnPropertyDescriptor(value, key)?.value;
}

function knownCode(value: unknown): string | number | null {
  if (typeof value === "number" && Number.isInteger(value) && value >= 100 && value <= 599) return value;
  if (typeof value === "string") return KNOWN_CODES.has(value) ? value : "unrecognized";
  return null;
}

function safeRequestId(value: unknown): string | null {
  return typeof value === "string" && /^(?:[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}|req_[a-zA-Z0-9]{8,100}|gen-\d+-[a-zA-Z0-9]{8,100})$/.test(value) && !/sk[-_]|bearer|secret|password|token/i.test(value) ? value : null;
}

/** Log known technical categories, never arbitrary provider messages; see AGENTS.md. */
export function providerErrorDiagnostic(error: unknown) {
  const body = field(error, "error");
  const metadata = field(body, "metadata");
  const raw = field(metadata, "raw");
  let providerError: unknown;
  if (typeof raw === "string" && Buffer.byteLength(raw, "utf8") <= MAX_RAW_ERROR_BYTES) {
    try {
      const parsed: unknown = JSON.parse(raw);
      providerError = field(parsed, "error") ?? parsed;
    } catch {
      // Unstructured provider text is classified without ever being returned.
    }
  }
  const reasonText = [field(error, "message"), field(body, "message"), field(providerError, "message"), typeof raw === "string" && raw.length <= MAX_RAW_ERROR_BYTES && providerError === undefined ? raw : undefined]
    .filter((value): value is string => typeof value === "string")
    .map((value) => value.slice(0, MAX_RAW_ERROR_BYTES))
    .join("\n");
  const reasons: string[] = [];
  if (/thought[_\s-]?signature|thinking.{0,30}signature|signature.{0,30}thinking/i.test(reasonText)) reasons.push("thought_signature");
  if (/context.{0,25}(length|limit|window)|too many (input )?tokens|input.{0,25}(too long|token limit)|token.{0,25}exceed/i.test(reasonText)) reasons.push("context_limit");
  if (/tool[_\s-]?(call|result|use)[_\s-]?id|tool.{0,50}(response|respond|pair|follow)|function.{0,20}response/i.test(reasonText)) reasons.push("tool_history");
  if (/schema|function[_\s-]?declaration|additionalProperties|INVALID_ARGUMENT.*tools/i.test(reasonText)) reasons.push("tool_schema");
  if (/tool_choice|function[_\s-]?calling|forced.{0,20}(tool|function)/i.test(reasonText)) reasons.push("tool_choice");
  if (/max_tokens|max_output_tokens|output.{0,20}token/i.test(reasonText)) reasons.push("output_token_limit");
  if (/safety|content[_\s-]?filter|blocked.{0,20}(content|prompt)/i.test(reasonText)) reasons.push("content_filter");
  if (/quota|rate[_\s-]?limit|resource.exhausted|capacity|overloaded/i.test(reasonText)) reasons.push("capacity_or_quota");
  if (/report remained invalid after/i.test(reasonText)) reasons.push("report_validation_failed");
  if (/correction attempt did not return a report/i.test(reasonText)) reasons.push("report_correction_missing");
  const provider = field(metadata, "provider_name");
  const param = field(error, "param");
  const paramRoot = typeof param === "string" ? param.split(/[.\[/]/, 1)[0] : undefined;
  return {
    status: knownCode(field(error, "status")),
    code: knownCode(field(error, "code")),
    type: knownCode(field(error, "type")),
    param: paramRoot !== undefined && KNOWN_PARAMS.has(paramRoot) ? paramRoot : null,
    requestId: safeRequestId(field(error, "requestID")),
    provider: typeof provider === "string" ? KNOWN_PROVIDERS.has(provider) ? provider : "unrecognized" : null,
    providerCode: knownCode(field(providerError, "code")),
    providerStatus: knownCode(field(providerError, "status")),
    reasons: reasons.length ? reasons : ["unclassified_provider_error"],
    rawErrorAvailable: raw !== undefined,
    rawErrorOversized: typeof raw === "string" && Buffer.byteLength(raw, "utf8") > MAX_RAW_ERROR_BYTES,
  };
}

/** Describe an incomplete completion without retaining model text or unknown tool names. */
export function incompleteReportDiagnostic(message: unknown, finishReason: unknown, usage: unknown) {
  const content = field(message, "content");
  const text = typeof content === "string" ? content.trim() : "";
  const jsonText = text.replace(/^```(?:json)?\s*\n?([\s\S]*?)\n?```$/i, "$1").trim();
  let parsedContent: unknown;
  if (jsonText.length <= 128_000 && jsonText.startsWith("{")) {
    try { parsedContent = JSON.parse(jsonText); } catch { /* Shape diagnostic only. */ }
  }
  const toolCalls = field(message, "tool_calls");
  const names = Array.isArray(toolCalls) ? toolCalls.map((call) => field(field(call, "function"), "name")) : [];
  const tokens = (name: string) => {
    const value = field(usage, name);
    return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : null;
  };
  return {
    messagePresent: message != null,
    finishReason: typeof finishReason === "string" && ["stop", "length", "tool_calls", "content_filter", "function_call"].includes(finishReason) ? finishReason : "unknown",
    textCharacterCount: typeof content === "string" ? content.length : 0,
    contentKind: !text ? "empty" : parsedContent !== undefined ? text.startsWith("```") ? "fenced_json" : "json_object" : text.startsWith("```") ? "fenced_text" : "text",
    contentMatchesReportSchema: parsedContent !== undefined && remediationReportSchema.safeParse(parsedContent).success,
    knownContentKeys: ["riskSummary", "observations", "remediation", "professionalConsultation", "name", "arguments", "parameters", "tool_calls", "aiGenerated", "webSources", "dataFreshness"].filter((key) => field(parsedContent, key) !== undefined),
    toolCallCount: names.length,
    reportToolCount: names.filter((name) => name === "remediation_report" || name === "generate_remediation_report").length,
    searchToolCount: names.filter((name) => name === "search_web").length,
    otherToolCount: names.filter((name) => name !== "remediation_report" && name !== "generate_remediation_report" && name !== "search_web").length,
    promptTokens: tokens("prompt_tokens"),
    completionTokens: tokens("completion_tokens"),
    totalTokens: tokens("total_tokens"),
  };
}
