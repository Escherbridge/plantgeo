import { describe, expect, it } from "vitest";
import { incompleteReportDiagnostic, providerErrorDiagnostic } from "@/lib/server/services/ai-provider-diagnostics";

function failure(message: string) {
  return {
    status: 400, code: 400, type: "invalid_request_error", param: "tools[0].function.parameters",
    requestID: "req_12345678abcd",
    error: { message: "Provider returned error", metadata: { provider_name: "Google AI Studio", raw: JSON.stringify({ error: { code: 400, status: "INVALID_ARGUMENT", message } }) } },
  };
}

describe("safe AI provider diagnostics", () => {
  it("distinguishes empty, text and structured completions without exposing output or unknown names", () => {
    expect(incompleteReportDiagnostic(undefined, undefined, undefined)).toMatchObject({ messagePresent: false, finishReason: "unknown", contentKind: "empty", reportToolCount: 0 });
    const secret = "sk-or-private-model-output";
    const diagnostic = incompleteReportDiagnostic({ content: secret, tool_calls: [{ function: { name: secret } }, { function: { name: "remediation_report" } }] }, secret, { prompt_tokens: 23, completion_tokens: 45, total_tokens: 68 });
    expect(diagnostic).toMatchObject({ finishReason: "unknown", contentKind: "text", contentMatchesReportSchema: false, toolCallCount: 2, reportToolCount: 1, otherToolCount: 1, promptTokens: 23, completionTokens: 45 });
    expect(JSON.stringify(diagnostic)).not.toContain(secret);
    const report = { riskSummary: { level: "low", headline: secret, factors: [], evidenceOrigin: "model_inference", evidenceSources: [] }, observations: [], remediation: [], professionalConsultation: "Consult a professional." };
    const structured = incompleteReportDiagnostic({ content: "```json\n" + JSON.stringify(report) + "\n```" }, "stop", {});
    expect(structured).toMatchObject({ contentKind: "fenced_json", contentMatchesReportSchema: true, knownContentKeys: ["riskSummary", "observations", "remediation", "professionalConsultation"] });
    expect(JSON.stringify(structured)).not.toContain(secret);
  });
  it.each([
    ["Function call is missing a thought_signature", "thought_signature"],
    ["Input exceeds the context length limit", "context_limit"],
    ["Each tool_call_id must have a response", "tool_history"],
    ["Invalid function declaration schema", "tool_schema"],
    ["tool_choice is not supported", "tool_choice"],
    ["max_tokens exceeds output limit", "output_token_limit"],
  ])("classifies technical failure without logging its raw message: %s", (message, reason) => {
    const diagnostic = providerErrorDiagnostic(failure(message));
    expect(diagnostic).toMatchObject({ status: 400, code: 400, type: "invalid_request_error", param: "tools", requestId: "req_12345678abcd", provider: "Google AI Studio", providerCode: 400, providerStatus: "INVALID_ARGUMENT" });
    expect(diagnostic.reasons).toContain(reason);
    expect(JSON.stringify(diagnostic)).not.toContain(message);
  });

  it("withholds credential and prompt echoes even in nominal diagnostic fields", () => {
    const secret = "sk-or-private-credential";
    const prompt = "Please analyze my confidential ranch";
    const error = failure(`Invalid schema for '${prompt}', Authorization Bearer ${secret}`);
    Object.assign(error, { message: `${prompt} ${secret}`, code: secret, type: prompt, param: prompt, requestID: secret, headers: { authorization: secret } });
    error.error.metadata.provider_name = prompt;
    error.error.metadata.raw = JSON.stringify({ error: { code: secret, status: prompt, message: `Invalid schema '${prompt}' ${secret}` }, original_input: { messages: [prompt], api_key: secret } });
    const diagnostic = providerErrorDiagnostic(error);
    expect(diagnostic.reasons).toContain("tool_schema");
    expect(JSON.stringify(diagnostic)).not.toContain(secret);
    expect(JSON.stringify(diagnostic)).not.toContain(prompt);
    expect(JSON.stringify(diagnostic)).not.toContain("original_input");
    expect(diagnostic.requestId).toBeNull();
  });

  it("does not dump unstructured or oversized provider errors and does not invoke getters", () => {
    const error = failure("unused");
    error.error.metadata.raw = "sk-or-hidden-secret raw context length failure";
    expect(providerErrorDiagnostic(error).reasons).toContain("context_limit");
    expect(JSON.stringify(providerErrorDiagnostic(error))).not.toContain("hidden-secret");
    error.error.metadata.raw = "x".repeat(16_385);
    expect(providerErrorDiagnostic(error)).toMatchObject({ rawErrorAvailable: true, rawErrorOversized: true, reasons: ["unclassified_provider_error"] });
    expect(() => providerErrorDiagnostic({ get error() { throw new Error("Do not inspect"); } })).not.toThrow();
  });
});
