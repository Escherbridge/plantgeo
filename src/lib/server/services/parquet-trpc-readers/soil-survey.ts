import { z } from "zod";
import { fetchBoundedJson, providerUrl } from "@/lib/server/http/bounded-upstream";
import {
  ParquetPlaneContractError,
  ParquetPlaneRequestError,
} from "@/lib/server/services/parquet-plane-client";

/**
 * The bridge to the admitted-release SSURGO route (soil-survey port S4).
 *
 * This is NOT a governed day-partitioned Parquet lane read (`shared.ts`'s `ParquetReaderResult`/
 * `boundedResult`/`mapEnvelope` machinery): SSURGO is a dedicated, static release with its own
 * route and its own availability vocabulary, so this module fetches it directly with
 * `fetchBoundedJson`, the same shape every other single-purpose upstream bridge in
 * `src/lib/server/services/` uses. See `src/lib/server/services/AGENTS.md` §soil-survey.
 *
 * The zod schema below is the FROZEN wire contract this reads: `services/agri-data-service/src/
 * agri_data_service/planes/soil_survey.py`'s `soil_survey_unavailable` and
 * `render_served_soil_survey`, served by `interface/http/soil_survey.py`'s `GET /query`. Read
 * those two functions before changing a field here -- this schema is a mirror, not a design.
 */

// [x, y] or [x, y, z]/[x, y, z, m]: Q4 ("repair else quarantine label and serve always") means an
// `invalid_unrepaired` row's original WKB reaches this port unrepaired, and DuckDB's
// `ST_GeomFromWKB`/`ST_AsGeoJSON` upstream (`planes/soil_survey.py::_filter_point_candidates_by_ring`
// names "a Z/M dimension" explicitly as a variant it can carry) can hand back a Z-bearing position
// or a ring under 4 points for a genuinely degenerate delineation. A 2-numbers-only, min(4) schema
// would fail `safeParse` on that one row and 503 the WHOLE viewport -- the opposite of "always
// serve" -- so this only checks a position is 2-4 finite numbers and a ring is non-empty.
const position = z.array(z.number().finite()).min(2).max(4);
const polygon = z.array(z.array(position).min(1)).min(1);
const geometry = z.discriminatedUnion("type", [
  z.object({ type: z.literal("Polygon"), coordinates: polygon }),
  z.object({ type: z.literal("MultiPolygon"), coordinates: z.array(polygon).min(1) }),
]);

/**
 * `_feature_from_row` in `planes/soil_survey.py`. Q4 ("repair else quarantine label and serve
 * always") is why `geometryQuality` exists on every feature rather than as a candidate-wide
 * summary: a native row is NEVER dropped, so there is no "unreadable geometry" state here at all
 * -- unlike the other proxied collections, an admitted-release answer never needs an
 * `unreadableGeometries` count.
 */
const soilSurveyFeatureProperties = z.object({
  mupolygonkey: z.string(),
  mukey: z.string(),
  muname: z.string().nullable(),
  soilSeries: z.string().nullable(),
  drainageClass: z.string().nullable(),
  // Tri-state boolean (`hydric_rating`, `warehouse/schemas/soil_survey.py`), never a string: an
  // unranked rating stays null rather than reading as a third string value.
  hydric: z.boolean().nullable(),
  landCapabilityClass: z.string().nullable(),
  // Nullable (`survey_area_symbol`, `warehouse/schemas/soil_survey.py`), unlike the non-nullable
  // `mupolygonkey`/`mukey` above -- a row can reach this port with no area symbol recorded.
  areaSymbol: z.string().nullable(),
  surveyAreaVintage: z.string(),
  // Nullable: `warehouse/schemas/soil_survey.py` -- "null only on pre-port rows" -- so a row
  // captured before Q4's repair/quarantine labelling landed can still reach this port unlabelled.
  geometryQuality: z.enum(["valid", "repaired", "invalid_unrepaired"]).nullable(),
  geometryRepresentation: z.literal("native"),
  source: z.literal("usda-sda"),
  releaseSha256: z.string().regex(/^[0-9a-f]{64}$/),
});

const soilSurveyFeature = z.object({
  type: z.literal("Feature"),
  id: z.string(),
  geometry,
  properties: soilSurveyFeatureProperties,
});

/** `render_served_soil_survey`'s `spatialCoverage`; present only on a `published` answer. */
const spatialCoverage = z.object({
  viewportAreas: z.array(z.string()),
  declaredAreaCount: z.number().int().nonnegative(),
  pendingAreaCount: z.number().int().nonnegative(),
});

const temporalScope = z.object({
  kind: z.literal("static_reference"),
  selectedDaySupported: z.literal(false),
});

/** `NATIVE_RUNG` in `foundation/soil_survey/release.py` -- the one rung this port ever serves. */
const NATIVE_SERVED_ZOOM = 13;

/** `MAX_VIEWPORT_ROWS` in `foundation/soil_survey/release.py`. */
const MAX_SOIL_SURVEY_FEATURES = 1000;

const collection = z
  .object({
    type: z.literal("FeatureCollection"),
    features: z.array(soilSurveyFeature).max(MAX_SOIL_SURVEY_FEATURES),
    availability: z.enum(["published", "unavailable"]),
    reason: z.string().nullable(),
    truncated: z.boolean(),
    revision: z
      .string()
      .regex(/^[0-9a-f]{64}$/)
      .nullable(),
    servedZoom: z.literal(NATIVE_SERVED_ZOOM),
    requestedZoom: z.number().int(),
    temporalScope,
    spatialCoverage: spatialCoverage.nullable(),
    releaseDay: z
      .string()
      .regex(/^\d{4}-\d{2}-\d{2}$/)
      .optional(),
    capturedAt: z.string().optional(),
  })
  .superRefine((value, context) => {
    if (
      value.availability === "published" &&
      (value.revision === null ||
        value.reason !== null ||
        value.spatialCoverage === null ||
        value.releaseDay === undefined ||
        value.capturedAt === undefined)
    ) {
      context.addIssue({
        code: "custom",
        message: "Published SSURGO response lacks pinned release evidence",
      });
    }
    if (
      value.availability === "unavailable" &&
      (value.features.length > 0 || value.reason === null || value.spatialCoverage !== null)
    ) {
      context.addIssue({
        code: "custom",
        message: "Unavailable SSURGO response cannot claim release evidence",
      });
    }
  });

export type ParquetSoilSurveyCollection = z.infer<typeof collection>;

/**
 * Read one admitted, static SSURGO release for a viewport; source acquisition never runs in this
 * request -- the release was captured, staged and admitted entirely offline (Go-3/Go-4,
 * `soil-survey-port-plan.md` §5).
 *
 * `date`/`day` are rejected before any fetch: `temporalScope.selectedDaySupported` is always
 * `false` on the wire, so a caller asking for one would get an answer that silently ignored it.
 */
export async function getParquetSoilSurvey(input: {
  bbox: string;
  zoom?: number;
  signal?: AbortSignal;
}): Promise<ParquetSoilSurveyCollection> {
  if ("date" in input || "day" in input) {
    throw new ParquetPlaneRequestError(
      "SSURGO is a static reference and does not support selected-day requests"
    );
  }
  const url = providerUrl("AGRI_PARQUET_SERVICE_URL", "http://localhost:8000");
  url.pathname = `${url.pathname.replace(/\/$/, "")}/api/v1/soil-survey/query`;
  url.search = "";
  url.searchParams.set("bbox", input.bbox);
  url.searchParams.set("zoom", String(Math.floor(input.zoom ?? NATIVE_SERVED_ZOOM)));
  const payload = await fetchBoundedJson(
    url,
    { method: "GET", headers: { Accept: "application/json" } },
    {
      timeoutMs: 15_000,
      maxBytes: 16 * 1024 * 1024,
      ...(input.signal === undefined ? {} : { signal: input.signal }),
    }
  );
  const parsed = collection.safeParse(payload);
  if (!parsed.success) {
    throw new ParquetPlaneContractError("SSURGO response violates the admitted release contract");
  }
  return parsed.data;
}
