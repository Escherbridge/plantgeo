import { z } from "zod";
import { daySchema, finiteNumberSchema, instantSchema } from "./shared";

/** The one immutable source manifest every frozen snapshot row must carry. */
export const SNAPSHOT_SOURCE_MANIFEST_SHA256 =
  "465abc4e813bf28c78acd7f97a4da9d19ad959e525de3eb1f422ca2f6e73e94f";

const sha256Schema = z.string().regex(/^[a-f0-9]{64}$/);

/** The normalized signal-plane row every climate-field and soil-field product publishes. */
export const signalPlaneRowSchema = z
  .object({
    support_key: z.string().min(1),
    signal_name: z.string().min(1),
    normalized_unit: z.string().min(1),
    cell_id: z.string().nullable(),
    observed_day: daySchema,
    normalized_value: finiteNumberSchema,
    observation_count: z.number().int().positive(),
    newest_observed_at: instantSchema,
    coverage_fraction: finiteNumberSchema.nullable(),
    allowed_client_exposure: z.boolean().nullable(),
    cell_longitude: finiteNumberSchema,
    cell_latitude: finiteNumberSchema,
  })
  .strict();

/** The signal plane plus the per-row source lineage a snapshot-lineage product pins. */
export const climateSnapshotLineageRowSchema = signalPlaneRowSchema
  .extend({
    source_key: z.string().min(1),
    source_parameter: z.string().min(1),
    source_snapshot_id: z.string().min(1),
    source_manifest_sha256: sha256Schema,
    precedence_contract: z.string().min(1),
    selected_source_row_id: z.number().int().nullable(),
    selected_source_row_sha256: sha256Schema.nullable(),
    selected_source_release_id: z.string().nullable(),
    selected_source_release_retrieved_at: instantSchema.nullable(),
    selected_source_release_payload_checksum: z.string().nullable(),
    selected_source_part_key: z.string().nullable(),
    selected_source_part_sha256: sha256Schema.nullable(),
    selected_source_row_ordinal: z.number().int().nonnegative().nullable(),
    input_source_row_count: z.number().int().positive(),
    input_source_row_digest: z.string().nullable(),
    input_source_row_ids: z.array(z.number().int()).nullable(),
    input_source_row_sha256s: z.array(sha256Schema).nullable(),
    input_source_release_ids: z.array(z.string()).nullable(),
    input_source_part_keys: z.array(z.string()).nullable(),
    input_source_part_sha256s: z.array(sha256Schema).nullable(),
    input_source_row_ordinals: z.array(z.number().int().nonnegative()).nullable(),
  })
  .strict();

const selectedSnapshotRowShape = {
  selected_observation_id: z.number().int().nullable(),
  selected_canonical_row_sha256: sha256Schema.nullable(),
  selected_source_release_id: z.string().nullable(),
  selected_release_retrieved_at: instantSchema.nullable(),
  physical_candidate_count: z.number().int().positive(),
  lineage_sha256: sha256Schema,
  input_manifest_sha256: sha256Schema,
};

export const soilWetnessRowSchema = signalPlaneRowSchema.extend(selectedSnapshotRowShape).strict();

export const soilTemperatureRowSchema = signalPlaneRowSchema
  .extend({
    data_source_key: z.string().min(1),
    source_parameter: z.string().min(1),
    ...selectedSnapshotRowShape,
  })
  .strict();

/** The columns a soil-field answer serves, shared by all three row contracts. */
export type SoilServingRow = Pick<
  z.infer<typeof signalPlaneRowSchema>,
  | "support_key"
  | "signal_name"
  | "normalized_unit"
  | "cell_id"
  | "observed_day"
  | "normalized_value"
  | "observation_count"
  | "newest_observed_at"
  | "coverage_fraction"
  | "allowed_client_exposure"
  | "cell_longitude"
  | "cell_latitude"
>;
