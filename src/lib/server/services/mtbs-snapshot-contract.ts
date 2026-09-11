import { z } from "zod";
import type { MtbsSnapshotMetadata } from "@/lib/environmental/mtbs-snapshot";

const daySchema = z.string().regex(/^\d{4}-\d{2}-\d{2}$/).refine((value) => {
  const instant = Date.parse(`${value}T00:00:00Z`);
  return Number.isFinite(instant) && new Date(instant).toISOString().slice(0, 10) === value;
});
const yearSchema = z.number().int().min(1984).max(2100);

/** Validated only as metadata; publication authority is checked by the Python reader. */
export const mtbsSnapshotWireSchema = z.object({
  schema: z.literal("mtbs-current-snapshot/v1"),
  manifest_sha256: z.string().regex(/^[a-f0-9]{64}$/),
  mode: z.literal("full_replacement"),
  available_day: daySchema,
  captured_from: z.string().datetime({ offset: true }),
  captured_through: z.string().datetime({ offset: true }),
  covered_years: z.object({ from: yearSchema, to: yearSchema }).strict(),
  bbox: z.tuple([
    z.number().finite().min(-180).max(180), z.number().finite().min(-90).max(90),
    z.number().finite().min(-180).max(180), z.number().finite().min(-90).max(90),
  ]),
  crs: z.literal("EPSG:4326"),
  capture_complete: z.literal(true),
  partial_fire_years: z.array(yearSchema).max(117),
  source_row_count: z.number().int().nonnegative().max(2000),
  source_url: z.literal("https://apps.fs.usda.gov/arcx/rest/services/EDW/EDW_MTBS_01/MapServer/63/query"),
}).strict().superRefine((value, ctx) => {
  const from = Date.parse(value.captured_from);
  const through = Date.parse(value.captured_through);
  const available = Date.parse(`${value.available_day}T00:00:00Z`);
  if (from > through || through - from > 600_000
    || !/(?:Z|[+]00:00)$/.test(value.captured_from)
    || !/(?:Z|[+]00:00)$/.test(value.captured_through)
    || available !== Date.parse(new Date(through).toISOString().slice(0, 10) + "T00:00:00Z") + 86_400_000
    || value.covered_years.from > value.covered_years.to
    || value.bbox[0] >= value.bbox[2] || value.bbox[1] >= value.bbox[3]
    || value.partial_fire_years.some((year, index, years) =>
      year < value.covered_years.from || year > value.covered_years.to
      || (index > 0 && year <= years[index - 1]))) {
    ctx.addIssue({ code: z.ZodIssueCode.custom, message: "Invalid MTBS snapshot scope or availability" });
  }
});

export function normalizeMtbsSnapshot(value: z.infer<typeof mtbsSnapshotWireSchema>): MtbsSnapshotMetadata {
  return {
    schema: value.schema, manifestSha256: value.manifest_sha256, mode: value.mode,
    availableDay: value.available_day, capturedFrom: value.captured_from, capturedThrough: value.captured_through,
    coveredYears: value.covered_years, bbox: value.bbox, crs: value.crs,
    captureComplete: value.capture_complete, partialFireYears: value.partial_fire_years,
    sourceRowCount: value.source_row_count, sourceUrl: value.source_url,
  };
}
