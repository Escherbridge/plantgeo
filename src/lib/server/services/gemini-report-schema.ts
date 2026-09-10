const INSTRUCTION_BOUNDS: Readonly<Record<string, string>> = {
  minLength: "Minimum string length",
  maxLength: "Maximum string length",
  minItems: "Minimum item count",
  maxItems: "Maximum item count",
};

/** Derive a smaller decoding schema while retaining bounds as instructions; see AGENTS.md. */
export function geminiReportSchema(schema: Record<string, unknown>): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  const instructions: string[] = [];
  for (const [key, value] of Object.entries(schema)) {
    const label = INSTRUCTION_BOUNDS[key];
    if (label && typeof value === "number") {
      instructions.push(`${label}: ${value}.`);
    } else if (Array.isArray(value)) {
      result[key] = value.map((item: unknown) => item !== null && typeof item === "object" && !Array.isArray(item) ? geminiReportSchema(item as Record<string, unknown>) : item);
    } else if (value !== null && typeof value === "object") {
      result[key] = geminiReportSchema(value as Record<string, unknown>);
    } else {
      result[key] = value;
    }
  }
  if (instructions.length) result.description = [typeof result.description === "string" ? result.description : "", ...instructions].filter(Boolean).join(" ");
  return result;
}
