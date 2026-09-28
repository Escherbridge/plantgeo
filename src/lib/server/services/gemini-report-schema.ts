const INSTRUCTION_BOUNDS: Readonly<Record<string, string>> = {
  minLength: "Minimum string length",
  maxLength: "Maximum string length",
  minItems: "Minimum item count",
  maxItems: "Maximum item count",
};

const ENUM_CHOICE_KEYS: ReadonlySet<string> = new Set(["enum", "anyOf", "oneOf"]);

const isSchemaObject = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value);

/**
 * The evidence-tool projection: bounds as instructions, and enum-array items as plain strings whose
 * allowed values move into the description. See AGENTS.md §gemini-forced-call-states (afternoon).
 */
export function geminiEvidenceSchema(schema: Record<string, unknown>): Record<string, unknown> {
  return geminiReportSchema(flattenEnumArrayItems(schema));
}

/** Each enum array is a repeating choice loop in Gemini's forced-call grammar; the loops sum across tools. */
function flattenEnumArrayItems(node: Record<string, unknown>): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(node)) {
    if (Array.isArray(value)) result[key] = value.map((item: unknown) => isSchemaObject(item) ? flattenEnumArrayItems(item) : item);
    else result[key] = isSchemaObject(value) ? flattenEnumArrayItems(value) : value;
  }
  const items = result.items;
  const isArray = result.type === "array" || (Array.isArray(result.type) && result.type.includes("array"));
  const allowed = isArray && isSchemaObject(items) ? enumChoices(items) : null;
  if (allowed && isSchemaObject(items)) {
    const itemsWithoutEnum = Object.fromEntries(Object.entries(items).filter(([key]) => !ENUM_CHOICE_KEYS.has(key)));
    if (!("type" in itemsWithoutEnum) && allowed.every((value) => typeof value === "string")) itemsWithoutEnum.type = "string";
    result.items = itemsWithoutEnum;
    const allowedValues = `Allowed values: ${allowed.map(String).join(", ")}.`;
    result.description = [typeof result.description === "string" ? result.description : "", allowedValues].filter(Boolean).join(" ");
  }
  return result;
}

/** An items schema's enum values, whether declared directly or as an anyOf/oneOf of enums; null otherwise. */
function enumChoices(items: Record<string, unknown>): unknown[] | null {
  if (Array.isArray(items.enum)) return items.enum;
  const branches = Array.isArray(items.anyOf) ? items.anyOf : Array.isArray(items.oneOf) ? items.oneOf : null;
  if (!branches?.length || !branches.every((branch) => isSchemaObject(branch) && Array.isArray(branch.enum))) return null;
  return branches.flatMap((branch) => (branch as { enum: unknown[] }).enum);
}

/** Derive a smaller decoding schema while retaining bounds as instructions; see AGENTS.md. */
export function geminiReportSchema(schema: Record<string, unknown>): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  const instructions: string[] = [];
  for (const [key, value] of Object.entries(schema)) {
    const label = INSTRUCTION_BOUNDS[key];
    if (label && typeof value === "number") {
      instructions.push(`${label}: ${value}.`);
      if ((key === "maxItems" && value === 0) || (key === "minItems" && value === 1)) result[key] = value;
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
