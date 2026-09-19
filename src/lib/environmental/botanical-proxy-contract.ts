import { z } from "zod";

/**
 * The published shape of `GET /api/botanical-occurrences`, declared ONCE for both sides of it.
 *
 * CLIENT-SAFE ON PURPOSE. The hook may not import
 * `src/lib/server/services/botanical-occurrences-client.ts` -- `src/lib/server/**` stays server-only
 * (typescript.md, "Boundaries and validation"), and a type-only import of a module that reads
 * server environment variables is exactly the kind of edge that stops being type-only after one
 * careless refactor. So the contract lives here, where a client component may read it.
 *
 * THE ROUTE PROVES IT AGREES AT COMPILE TIME. `route.ts` assigns its decoded answer to
 * `BotanicalProxyAnswer` before responding, so if the server client's decoded shape ever drifts
 * from this schema the typecheck fails rather than a hook silently receiving a field it cannot
 * find. That is why this is a duplicate worth having: it is a checked one.
 *
 * CAMELCASE, because this is the decoded vocabulary the server client produces -- NOT the plane's
 * snake_case wire. `botanical-presentation.ts` is the seam back to the layer components' snake_case
 * vocabulary and stays the only place that conversion happens.
 */

/** A GeoJSON polygon ring set, typed loosely enough that `GeoJSON.Polygon` satisfies it. */
const polygonSchema = z.object({
  type: z.literal("Polygon"),
  coordinates: z.array(z.array(z.array(z.number()))),
});

/** One detail-zoom specimen record. */
export const botanicalProxyFeatureSchema = z.object({
  occurrenceId: z.string(),
  collectionKey: z.string(),
  sourceRecordKey: z.string(),
  taxonConceptId: z.string(),
  resolutionState: z.string(),
  scientificName: z.string().nullable(),
  family: z.string().nullable(),
  eventInterval: z.object({
    start: z.string().nullable(),
    end: z.string().nullable(),
    precision: z.string().nullable(),
  }),
  longitude: z.number(),
  latitude: z.number(),
  coordinateUncertaintyMeters: z.number().nullable(),
  spatialClass: z.string(),
  membership: z.enum(["confirmed", "possible"]).nullable(),
  catalogNumber: z.string().nullable(),
  recordedBy: z.string().nullable(),
  basisOfRecord: z.string().nullable(),
  rightsUri: z.string().nullable(),
  attributionText: z.string().nullable(),
});

/** One aggregate support cell. */
export const botanicalProxyCellSchema = z.object({
  cellId: z.string(),
  geometry: polygonSchema,
  evaluation: z.string(),
  documentedTaxa: z.number(),
  recordCount: z.number(),
  eventEstimate: z.number().nullable(),
  collectionCount: z.number(),
  excludedByQc: z.number(),
  possibleOnlyRecords: z.number(),
});

/**
 * The §4a pointer provenance the answer was pinned from. Optional because the server client marks
 * it optional for the benefit of directly-constructed values; the route always sends it.
 */
export const botanicalProxyPointerSchema = z.object({
  generationId: z.string(),
  manifestChecksum: z.string(),
  manifestKey: z.string(),
  /** Which pointer answered. `latest_v1` is the only kind; the legacy `current.json` bridge is retired. */
  pointerKind: z.enum(["latest_v1"]),
  pointerSchemaVersion: z.number(),
  pointerWrittenAt: z.string().nullable(),
  publishedAt: z.string().nullable(),
});

/** The three published rungs, in the plane's own `support_id` vocabulary. */
export const botanicalServingRungSchema = z.enum(["detail", "grid-0.05", "grid-0.25"]);

const proxyAnswerBase = {
  /**
   * WHICH RUNG ANSWERED, always stated. The route selects it from zoom AND bbox size
   * (`botanicalServingBandForViewport`), so a wide viewport at a detail zoom is answered from a
   * coarser rung rather than refused -- and a reader must be able to tell that happened. Without
   * this field a caption would have to infer the rung from the zoom it asked with, which is exactly
   * the inference the owner decision of 2026-09-18 made wrong.
   */
  servingRung: botanicalServingRungSchema,
  releaseSetId: z.string(),
  publishedAt: z.string().nullable(),
  taxonomyRecipeVersion: z.string().nullable(),
  qcPolicyVersion: z.string().nullable(),
  truncated: z.boolean(),
  nextCursor: z.string().nullable(),
  pointer: botanicalProxyPointerSchema.optional(),
};

/** A successful proxy answer: the plane's `detail` or `aggregate` state, decoded. */
export const botanicalProxyAnswerSchema = z.discriminatedUnion("state", [
  z.object({
    ...proxyAnswerBase,
    state: z.literal("detail"),
    features: z.array(botanicalProxyFeatureSchema),
    counts: z.object({
      returned: z.number(),
      matched: z.number(),
      withheld: z.number(),
      nonspatial: z.number(),
      excludedByQc: z.number(),
    }),
  }),
  z.object({
    ...proxyAnswerBase,
    state: z.literal("aggregate"),
    supportId: z.string(),
    cells: z.array(botanicalProxyCellSchema),
    counts: z.object({ returned: z.number(), matched: z.number() }),
  }),
]);

/**
 * The route's ONE error shape. `reason` is the machine-readable half and carries the plane's own
 * refusal reasons and the five §4a pointer failures unchanged, so a caller never has to infer
 * "nothing was ever published" from a status code shared with "the bytes under the pointer changed".
 */
export const botanicalProxyErrorSchema = z.object({
  error: z.string(),
  reason: z.string(),
  detail: z.string().optional(),
});

export type BotanicalProxyFeature = z.infer<typeof botanicalProxyFeatureSchema>;
export type BotanicalProxyCell = z.infer<typeof botanicalProxyCellSchema>;
export type BotanicalProxyPointer = z.infer<typeof botanicalProxyPointerSchema>;
export type BotanicalServingRung = z.infer<typeof botanicalServingRungSchema>;
export type BotanicalProxyAnswer = z.infer<typeof botanicalProxyAnswerSchema>;
export type BotanicalProxyError = z.infer<typeof botanicalProxyErrorSchema>;

/** The route's path, spelled once so the hook and its tests cannot disagree with the handler. */
export const BOTANICAL_OCCURRENCES_PROXY_PATH = "/api/botanical-occurrences";
