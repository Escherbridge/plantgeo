import { TRPCError } from "@trpc/server";
import { z } from "zod";
import { router, publicProcedure } from "@/lib/server/trpc/init";
import {
  UpstreamHttpError,
  UpstreamPayloadError,
  UpstreamTimeoutError,
} from "@/lib/server/http/bounded-upstream";
import { getInterventionSuitability } from "@/lib/server/services/carbon-potential";
import {
  getMetricAtDate,
  getPublishedDroughtClassification,
  getPublishedGroundwaterWells,
  getPublishedStreamflowGauges,
  getPublishedVegetationIndex,
  getSliderCapabilities,
} from "@/lib/server/services/environmental-read-model";
import {
  getWatersheds,
  MAX_WATERSHED_BBOX_SQUARE_DEGREES,
  WatershedResponseError,
} from "@/lib/server/services/hydrosheds";
import { NLCD_CLASSES } from "@/lib/server/services/nlcd";
import {
  getSoilProperties,
  SoilEvidenceUnavailableError,
  SoilUpstreamUnavailableError,
} from "@/lib/server/services/soilgrids";
import {
  getSoilSurvey,
  soilSurveyAreaCeiling,
  SoilSurveyResponseError,
  type SoilSurveyGranularity,
} from "@/lib/server/services/usda-soil";
import {
  GIBS_NDVI_PRODUCT,
  getEnvironmentalTileTemplate,
  getNDVITileUrl,
  getNDWITileUrl,
  resolveGibsNdviDate,
} from "@/lib/vegetation";

const COORDINATE_PATTERN = /^-?(?:\d+(?:\.\d*)?|\.\d+)$/;

const pointSchema = z.object({
  lat: z.number().min(-90).max(90),
  lon: z.number().min(-180).max(180),
});

const bboxSchema = z
  .string()
  .trim()
  .min(7)
  .max(100)
  .superRefine((value, context) => {
    const raw = value.split(",");
    if (raw.length !== 4 || raw.some((part) => !COORDINATE_PATTERN.test(part))) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        message: 'Invalid bbox format: expected "west,south,east,north"',
      });
      return;
    }
    const [west, south, east, north] = raw.map(Number);
    if (west < -180 || east > 180 || south < -90 || north > 90) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        message: "Bounding box is outside WGS84 bounds",
      });
    }
    if (west >= east || south >= north) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        message: "Bounding box must have positive width and height",
      });
    }
  });

/**
 * The day the map is drawing, as every warehouse-backed viewport read takes it.
 *
 * Optional everywhere, and OMITTING it is not the same as passing today: an omitted day means
 * "the live edge", which is the query each reader has always run and the one the client keeps
 * its existing cache entry for. See `src/lib/server/AGENTS.md` §slider-day.
 */
const observationDateSchema = z
  .string()
  .regex(/^\d{4}-\d{2}-\d{2}$/, "Date must be YYYY-MM-DD");

/**
 * Caps the viewport area for a procedure that proxies a third-party API per request.
 * `bboxSchema` bounds WGS84 legality, not extent; these two procedures are
 * unauthenticated and cache per exact bbox, so nothing amortizes a basin-wide ask.
 * See `src/lib/server/AGENTS.md` §soil-survey and §watershed-boundaries for the
 * measurements behind each ceiling.
 */
function areaBoundedBbox(maxSquareDegrees: number) {
  return bboxSchema.superRefine((value, context) => {
    const [west, south, east, north] = value.split(",").map(Number);
    const area = (east - west) * (north - south);
    if (Number.isFinite(area) && area > maxSquareDegrees) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        message: `Bounding box exceeds ${maxSquareDegrees} square degrees; zoom in`,
      });
    }
  });
}

function unavailableCollection(reason: string) {
  return {
    type: "FeatureCollection" as const,
    features: [] as GeoJSON.Feature[],
    availability: "unavailable" as const,
    reason,
    truncated: false,
    unreadableGeometries: 0,
    observedAt: null,
    revision: null,
  };
}

/**
 * A collection proxied live from a third-party provider rather than read from the
 * warehouse, carrying the same availability/reason pair `PublishedDroughtCollection`
 * does so an empty viewport stays distinguishable from a withheld capability.
 */
export interface ProxiedFeatureCollection extends GeoJSON.FeatureCollection {
  availability: "published" | "unavailable";
  reason: string | null;
  /**
   * The provider held more features than it served. Published but partial: the count
   * and the extent below it are a subset, not the whole viewport.
   */
  truncated: boolean;
  /**
   * Features the provider did serve that this reader could not turn into geometry and
   * dropped. Also published but partial, and for a reason the provider never reported:
   * an empty collection with a non-zero count here is an unreadable-geometry gap, not a
   * viewport the survey found nothing in.
   */
  unreadableGeometries: number;
  observedAt: string | null;
  revision: string | null;
}

/**
 * The SSURGO response, which additionally reports WHICH granularity answered.
 *
 * Its own type rather than an optional field on `ProxiedFeatureCollection`: the watershed feed
 * has no granularity at all, and an optional one there would let a caller test for a property
 * that can never be set. `SoilPanel` reads this to caption an averaged view as an average
 * rather than as a surveyed map unit -- see `src/lib/server/AGENTS.md` §soil-survey-zoom.
 */
export interface ProxiedSoilSurveyCollection extends ProxiedFeatureCollection {
  granularity: SoilSurveyGranularity;
}

/**
 * Maps a bounded-fetch transport fault onto the retryable code, mirroring how
 * `getSoilProperties` below treats `SoilUpstreamUnavailableError`. Anything else --
 * a permanent 4xx, a configuration fault, a cache failure -- propagates unchanged
 * rather than being relabelled as a temporary outage the client should retry.
 */
function rethrowUpstreamFault(error: unknown, provider: string): never {
  const isTransient =
    error instanceof UpstreamTimeoutError ||
    error instanceof UpstreamPayloadError ||
    (error instanceof UpstreamHttpError &&
      (error.status === 429 || error.status >= 500));
  if (isTransient) {
    throw new TRPCError({
      code: "SERVICE_UNAVAILABLE",
      message: `${provider} is temporarily unavailable`,
    });
  }
  throw error;
}

export const environmentalRouter = router({
  getNDVIMetadata: publicProcedure
    .input(
      z.object({
        year: z.number().int().min(2000).max(2100),
        month: z.number().int().min(1).max(12),
        mode: z.enum(["absolute", "anomaly"]).default("absolute"),
      })
    )
    .query(({ input }) => {
      const tileUrl = getNDVITileUrl(input.year, input.month, input.mode);
      return {
        tileUrl,
        availability: tileUrl ? ("published" as const) : ("unavailable" as const),
        reason: tileUrl
          ? null
          : input.mode === "anomaly"
            ? ("ndvi_anomaly_baseline_not_published" as const)
            : ("gibs_ndvi_coverage_not_available_for_month" as const),
        year: input.year,
        month: input.month,
        mode: input.mode,
        // The composite the tile actually represents -- 8 days ending at this
        // date, not the whole month -- so the client can label it honestly.
        compositeDate: resolveGibsNdviDate(input.year, input.month),
        compositeWindowDays: GIBS_NDVI_PRODUCT.compositeWindowDays,
        maxZoom: GIBS_NDVI_PRODUCT.maxZoom,
        attribution: GIBS_NDVI_PRODUCT.attribution,
      };
    }),

  getVegetationSources: publicProcedure
    .input(
      z.object({
        year: z.number().int().min(2000).max(2100).default(new Date().getFullYear()),
        month: z.number().int().min(1).max(12).default(new Date().getMonth() + 1),
      })
    )
    .query(({ input }) => {
      const ndvi = getNDVITileUrl(input.year, input.month, "absolute");
      const anomaly = getNDVITileUrl(input.year, input.month, "anomaly");
      const ndwi = getNDWITileUrl(input.year, input.month);
      const nlcd = getEnvironmentalTileTemplate(
        "land-cover/nlcd-2021/{z}/{x}/{y}.png"
      );
      // Availability is reported per product: NDVI is served by GIBS while
      // NDWI, the NDVI anomaly, and the first-party land-cover release are all
      // genuinely unpublished. A single collapsed flag would either hide the
      // working layer or overstate the missing ones.
      return {
        availability: ndvi ? ("partial" as const) : ("unavailable" as const),
        ndvi: {
          tileUrl: ndvi,
          availability: ndvi ? ("published" as const) : ("unavailable" as const),
          maxZoom: GIBS_NDVI_PRODUCT.maxZoom,
          compositeDate: resolveGibsNdviDate(input.year, input.month),
          compositeWindowDays: GIBS_NDVI_PRODUCT.compositeWindowDays,
          anomalyTileUrl: anomaly,
          anomalyAvailability: anomaly
            ? ("published" as const)
            : ("unavailable" as const),
          anomalyReason: "ndvi_anomaly_baseline_not_published" as const,
          attribution: GIBS_NDVI_PRODUCT.attribution,
        },
        ndwi: {
          tileUrl: ndwi,
          availability: ndwi ? ("published" as const) : ("unavailable" as const),
          reason: "no_public_ndwi_raster_product_exists" as const,
          attribution: null,
        },
        nlcd: {
          tileUrl: nlcd,
          changeTileUrl: getEnvironmentalTileTemplate(
            "land-cover/nlcd-change/{z}/{x}/{y}.png"
          ),
          availability: nlcd ? ("published" as const) : ("unavailable" as const),
          reason: "first_party_land_cover_release_not_published" as const,
          classes: Object.values(NLCD_CLASSES),
        },
      };
    }),

  getReforestationZones: publicProcedure
    .input(z.object({ bbox: bboxSchema }))
    .query(() =>
      unavailableCollection("validated_reforestation_output_not_published")
    ),

  /**
   * USGS gauge readings for the viewport: the live edge, or one named day's newest reading
   * per gauge. A day the record has nothing for answers with an empty array; the layer's own
   * slider capability is what captions it.
   */
  getStreamflow: publicProcedure
    .input(z.object({ bbox: bboxSchema, date: observationDateSchema.optional() }))
    .query(({ input }) => getPublishedStreamflowGauges(input.bbox, input.date)),

  /**
   * The newest published NDVI observation per sampling-grid cell in a viewport.
   *
   * bbox is required, unlike getDroughtClassification's: `vegetation` is the largest layer
   * in the warehouse and is a four-year daily series rather than a snapshot, so there is no
   * honest no-argument answer. The reader collapses the series to one row per grid cell and
   * publishes both of its bounds (`maxCellCount`, `maxObservationAgeDays`) in the payload.
   *
   * Deliberately NOT wrapped in areaBoundedBbox: the two procedures that cap viewport area
   * do so because each one proxies a third-party API per request. This one reads the local
   * warehouse and is bounded by the grid itself -- a whole-world bbox answers with the same
   * 1,568 cells a regional one does.
   *
   * `date` slides the 30-day observation window to end at that day rather than at now.
   */
  getVegetationIndex: publicProcedure
    .input(z.object({ bbox: bboxSchema, date: observationDateSchema.optional() }))
    .query(({ input }) => getPublishedVegetationIndex(input.bbox, input.date)),

  /**
   * Serves the stored USDM release covering a day, clipped and generalized in PostGIS.
   * bbox is optional so the existing no-argument callers keep working; passing
   * one returns far less geometry and is strongly preferred. `date` is resolved through the
   * same bounded weekly carry-forward the slider metric uses, so a release week the record
   * skips renders empty rather than borrowing the week before it.
   */
  getDroughtClassification: publicProcedure
    .input(
      z
        .object({
          bbox: bboxSchema.optional(),
          date: observationDateSchema.optional(),
        })
        .optional()
    )
    .query(({ input }) =>
      getPublishedDroughtClassification(input?.bbox, input?.date)
    ),

  /**
   * HUC12 watershed boundaries for the viewport, proxied live from USGS NHD+ HR and
   * cached in Redis for an hour by the service.
   */
  getWatersheds: publicProcedure
    .input(z.object({ bbox: areaBoundedBbox(MAX_WATERSHED_BBOX_SQUARE_DEGREES) }))
    .query(async ({ input }): Promise<ProxiedFeatureCollection> => {
      try {
        const collection = await getWatersheds(input.bbox);
        return {
          ...collection,
          availability: "published",
          reason: null,
          // hydrosheds validates the payload as a whole and rejects it rather than
          // dropping individual features, so nothing here is ever silently unreadable.
          unreadableGeometries: 0,
          // The provider publishes no release timestamp for the boundary set, so
          // there is nothing honest to put here.
          observedAt: null,
          revision: null,
        };
      } catch (error) {
        // ArcGIS answers some faults with HTTP 200 and an `error` object. Reporting
        // that as an empty viewport would claim the provider said there are no
        // watersheds here; it is a provider fault, not a coverage answer.
        if (error instanceof WatershedResponseError) {
          return unavailableCollection("watershed_upstream_returned_no_features");
        }
        rethrowUpstreamFault(error, "The USGS hydrography service");
      }
    }),

  /**
   * Reserved: no groundwater observation is published on any day. `date` is accepted so the
   * client can pass the slider's day uniformly, not because it narrows anything yet.
   */
  getGroundwater: publicProcedure
    .input(z.object({ bbox: bboxSchema, date: observationDateSchema.optional() }))
    .query(({ input }) => getPublishedGroundwaterWells(input.bbox, input.date)),

  getWaterScarcityScore: publicProcedure
    .input(z.object({ bbox: bboxSchema }))
    .query(() => ({
      availability: "unavailable" as const,
      reason: "validated_water_scarcity_output_not_published" as const,
      score: null,
      components: null,
      revision: null,
    })),

  getSoilProperties: publicProcedure
    .input(pointSchema)
    .query(async ({ input }) => {
      try {
        return await getSoilProperties(input.lat, input.lon);
      } catch (error) {
        if (error instanceof SoilEvidenceUnavailableError) {
          throw new TRPCError({
            code: "PRECONDITION_FAILED",
            message: error.message,
          });
        }
        // Transient upstream fault: the client may retry, unlike a coverage gap.
        if (error instanceof SoilUpstreamUnavailableError) {
          throw new TRPCError({
            code: "SERVICE_UNAVAILABLE",
            message: error.message,
          });
        }
        throw error;
      }
    }),

  /**
   * SSURGO map-unit polygons for the viewport, proxied live from USDA Soil Data
   * Access and cached in Redis for a day by the service.
   *
   * The area ceiling is zoom-dependent, so it cannot live on the bbox field the way
   * `areaBoundedBbox` puts it: `soilSurveyAreaCeiling` returns the original measured ceiling
   * only for the detail tier, which makes one uncapped SDA call, and null for the aggregated
   * tiers, which tile themselves inside that same per-cell ceiling and degrade to
   * `truncated: true` rather than erroring. See `usda-soil.ts` §soil-survey-zoom.
   */
  getSoilSurvey: publicProcedure
    .input(
      z
        .object({
          bbox: bboxSchema,
          /** Viewport zoom; selects render granularity and the ceiling that applies. */
          zoom: z.number().finite().optional(),
        })
        .superRefine((value, context) => {
          const ceiling = soilSurveyAreaCeiling(value.zoom);
          if (ceiling === null) return;
          const [west, south, east, north] = value.bbox.split(",").map(Number);
          const area = (east - west) * (north - south);
          if (Number.isFinite(area) && area > ceiling) {
            context.addIssue({
              code: z.ZodIssueCode.custom,
              message: `Bounding box exceeds ${ceiling} square degrees; zoom in`,
            });
          }
        })
    )
    .query(async ({ input }): Promise<ProxiedSoilSurveyCollection> => {
      try {
        const collection = await getSoilSurvey(input.bbox, input.zoom);
        return {
          ...collection,
          availability: "published",
          reason: null,
          // Map units SDA served that would not parse. Carried rather than absorbed:
          // SoilPanel must not caption a reader gap as ground USDA found no soil on.
          unreadableGeometries: collection.unreadableGeometries,
          // Which tier actually answered, so an averaged view is never captioned as a
          // surveyed map unit.
          granularity: collection.granularity,
          // SSURGO's survey areas each carry their own vintage; the response exposes
          // no single publication time for the returned map units.
          observedAt: null,
          revision: null,
        };
      } catch (error) {
        if (error instanceof SoilSurveyResponseError) {
          return {
            ...unavailableCollection("soil_survey_upstream_returned_no_table"),
            // A provider fault answered nothing, so no tier described the viewport. The
            // detail tier is the honest default: it is what a zoomless request resolves to.
            granularity: "detail",
          };
        }
        rethrowUpstreamFault(error, "USDA Soil Data Access");
      }
    }),

  getInterventionSuitability: publicProcedure
    .input(pointSchema)
    .query(({ input }) => getInterventionSuitability(input.lat, input.lon)),

  /**
   * What the time slider may offer, and the server's UTC today.
   * The client must take "today" from here and never from its own clock, or a
   * browser in another timezone silently disagrees about which days are future.
   */
  getSliderCapabilities: publicProcedure.query(() => getSliderCapabilities()),

  /**
   * One layer's metric for one day. Returns an availability/reason pair rather
   * than a bare empty collection, so "nothing observed that day" is legible as
   * something other than a failure.
   */
  getMetricAtDate: publicProcedure
    .input(
      z.object({
        metric: z.string().trim().min(1).max(64),
        // Required here, unlike the viewport reads above: this procedure exists to answer
        // "what did this metric read on this day", so there is no live-edge default.
        date: observationDateSchema,
        variant: z.enum(["observed", "monte_carlo", "ml"]).default("observed"),
        bbox: bboxSchema.optional(),
      })
    )
    .query(({ input }) => getMetricAtDate(input)),
});
