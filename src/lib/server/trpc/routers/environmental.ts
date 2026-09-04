import { TRPCError } from "@trpc/server";
import { z } from "zod";
import { router, publicProcedure } from "@/lib/server/trpc/init";
import { rethrowUpstreamFault } from "@/lib/server/trpc/upstream-fault";
import { getInterventionSuitability } from "@/lib/server/services/carbon-potential";
import { getPublishedRasters } from "@/lib/server/services/raster-catalog";
import {
  getMetricAtDate,
  getPublishedGroundwaterWells,
} from "@/lib/server/services/environmental-read-model";
import { parquetClimateFieldCollection } from "@/lib/server/services/parquet-climate-field";
import { getParquetSliderCapabilities } from "@/lib/server/services/parquet-slider-capabilities";
import {
  getParquetBurnSeverity,
  getParquetDrought,
  getParquetClimateField,
  getParquetEvacuationZones,
  getParquetFirePerimeters,
  getParquetSensorStations,
  getParquetSoilField,
  getParquetVegetation,
  getParquetWaterGauges,
  getParquetWatersheds,
  parquetUpstreamFailure,
  rejectAborted,
} from "@/lib/server/services/parquet-trpc-readers";
import {
  AIR_TEMPERATURE_VARIANT_IDS,
  CLIMATE_FIELD_SIGNAL_IDS,
  CLIMATE_RENDER_FORMS,
  DEFAULT_AIR_TEMPERATURE_VARIANT,
  DEFAULT_CLIMATE_FIELD_SIGNAL,
  type AirTemperatureVariant,
  type ClimateFieldSignalId,
  type ClimateRenderForm,
} from "@/lib/environmental/climate-field";
import {
  SOIL_FIELD_DEPTHS,
  SOIL_FIELD_MEASURE_IDS,
  type SoilFieldDepth,
  type SoilFieldMeasure,
} from "@/lib/environmental/soil-field";
import {
  getWatersheds,
  MAX_WATERSHED_BBOX_SQUARE_DEGREES,
  WatershedResponseError,
} from "@/lib/server/services/hydrosheds";
import { NLCD_CLASSES } from "@/lib/server/services/nlcd";
import { METRIC_AT_DATE_IDS } from "@/types/time-slider";
import {
  getSoilProperties,
  SoilEvidenceUnavailableError,
  SoilUpstreamUnavailableError,
} from "@/lib/server/services/soilgrids";
import {
  getSoilSurvey,
  soilSurveyAreaCeiling,
  SoilSurveyResponseError,
  type SoilSurveyCoverage,
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

/** Viewport zoom is a required serving coordinate, not an optional rendering hint. */
const mapZoomSchema = z.number().finite().nonnegative();

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
  /**
   * How much of the viewport the warehouse can answer for. The gap persistence created:
   * ground nobody has fetched paints exactly like ground the survey found nothing on, and
   * `covered < cells` is the only thing that tells them apart. See
   * `usda-soil.ts#SoilSurveyCoverage`.
   */
  coverage: SoilSurveyCoverage;
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

  /**
   * Water-gauge rows from exactly one private Parquet rung. Coarse rungs intentionally carry
   * anonymous cell means; the four public states keep an empty answer distinct from a gap.
   */
  getStreamflow: publicProcedure
    .input(
      z.object({
        bbox: bboxSchema,
        date: observationDateSchema.optional(),
        zoom: mapZoomSchema,
      })
    )
    .query(async ({ input, signal }) =>
      rejectAborted(
        await getParquetWaterGauges({
          bbox: input.bbox,
          date: input.date,
          mapZoom: input.zoom,
          signal,
        })
      )
    ),

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
    .input(
      z.object({
        bbox: bboxSchema,
        date: observationDateSchema.optional(),
        zoom: mapZoomSchema,
      })
    )
    .query(async ({ input, signal }) =>
      rejectAborted(
        await getParquetVegetation({
          bbox: input.bbox,
          date: input.date,
          mapZoom: input.zoom,
          signal,
        })
      )
    ),

  /**
   * Serves the newest private-Parquet USDM release at or before the requested day. The
   * adapter preserves the release's own served day and never relabels it as the request day.
   */
  getDroughtClassification: publicProcedure
    .input(
      z.object({
        bbox: bboxSchema.optional(),
        date: observationDateSchema.optional(),
        zoom: mapZoomSchema,
      })
    )
    .query(async ({ input, signal }) =>
      rejectAborted(
        await getParquetDrought({
          bbox: input.bbox,
          date: input.date,
          mapZoom: input.zoom,
          signal,
        })
      )
    ),

  /**
   * The five `geo.features` layers that drew from Martin tile functions until the
   * environmental_postgres_retirement_20260904 track, now read from the private Parquet plane
   * exactly as drought is. Four moved in wave C; `getFirePerimeters` followed once its lane was
   * re-registered `static_lookup`, and with it the last environmental read left PostgreSQL.
   *
   * All five take the same `(bbox, date, zoom)` triple every other Parquet viewport read takes,
   * and for the same reasons: `bbox` clips the geometry server-side (`_clipped_scan` in
   * `parquet_ops/warehouse_reader.py`), and `zoom` SELECTS the published rung rather than hinting
   * at one -- which is the whole point of the cutover, because the tile functions they replace did
   * no simplification at any zoom. `geo.burn_severity_tiles()` was measured at 2,341,323 vertices
   * / 37.5 MB / 28.4 s cold for one read of the whole layer.
   *
   * Deliberately NOT wrapped in `areaBoundedBbox`: that ceiling exists for the two procedures that
   * proxy a third-party API per request. These read the local warehouse at a rung chosen for the
   * camera, so a whole-world bbox at z0 is cheaper than a city block at z13, not more expensive.
   */
  getEvacuationZones: publicProcedure
    .input(
      z.object({
        bbox: bboxSchema.optional(),
        date: observationDateSchema.optional(),
        zoom: mapZoomSchema,
      })
    )
    .query(async ({ input, signal }) =>
      rejectAborted(
        await getParquetEvacuationZones({
          bbox: input.bbox,
          date: input.date,
          mapZoom: input.zoom,
          signal,
        })
      )
    ),

  /**
   * `date` is the slider day the caller wants the incident set AS OF, and it is not optional
   * decoration: the reader resolves it to the newest snapshot at or before it and then filters
   * that snapshot in frame on the same day. An omitted date means the live edge, as everywhere
   * else here.
   */
  getFirePerimeters: publicProcedure
    .input(
      z.object({
        bbox: bboxSchema.optional(),
        date: observationDateSchema.optional(),
        zoom: mapZoomSchema,
      })
    )
    .query(async ({ input, signal }) =>
      rejectAborted(
        await getParquetFirePerimeters({
          bbox: input.bbox,
          date: input.date,
          mapZoom: input.zoom,
          signal,
        })
      )
    ),

  getBurnSeverity: publicProcedure
    .input(
      z.object({
        bbox: bboxSchema.optional(),
        date: observationDateSchema.optional(),
        zoom: mapZoomSchema,
      })
    )
    .query(async ({ input, signal }) =>
      rejectAborted(
        await getParquetBurnSeverity({
          bbox: input.bbox,
          date: input.date,
          mapZoom: input.zoom,
          signal,
        })
      )
    ),

  getSensorStations: publicProcedure
    .input(
      z.object({
        bbox: bboxSchema.optional(),
        date: observationDateSchema.optional(),
        zoom: mapZoomSchema,
      })
    )
    .query(async ({ input, signal }) =>
      rejectAborted(
        await getParquetSensorStations({
          bbox: input.bbox,
          date: input.date,
          mapZoom: input.zoom,
          signal,
        })
      )
    ),

  /**
   * The DRAWN basin set, distinct from `getWatersheds` below, which answers the Watersheds panel's
   * basin LIST by proxying USGS live under a one-square-degree ceiling. Two procedures because
   * they answer two questions: the map needs every basin the camera covers at the rung it can
   * draw, and the panel needs the handful of basins a reader is looking at, named by the upstream.
   */
  getWatershedBoundaries: publicProcedure
    .input(
      z.object({
        bbox: bboxSchema.optional(),
        date: observationDateSchema.optional(),
        zoom: mapZoomSchema,
      })
    )
    .query(async ({ input, signal }) =>
      rejectAborted(
        await getParquetWatersheds({
          bbox: input.bbox,
          date: input.date,
          mapZoom: input.zoom,
          signal,
        })
      )
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
   * SSURGO map-unit polygons for the viewport, read from the warehouse. Cells nobody has
   * fetched yet are warmed from USDA Soil Data Access first, bounded per request; see
   * `usda-soil.ts` §soil-survey-persistence.
   *
   * The area ceiling is zoom-dependent, so it cannot live on the bbox field the way
   * `areaBoundedBbox` puts it: `soilSurveyAreaCeiling` returns the original measured ceiling
   * only for the detail tier, which may warm at most a 2x2 patch of cells, and null for the
   * aggregated tiers, which cap their own cell budget and degrade to `truncated: true`
   * rather than erroring. See `usda-soil.ts` §soil-survey-zoom.
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
          // How much of the viewport the store actually covers. Carried, never absorbed:
          // unfetched ground draws exactly like unsurveyed ground.
          coverage: collection.coverage,
          // SSURGO's survey areas each carry their own vintage (per feature, as
          // `surveyAreaVintage`); the product publishes no single release timestamp for a
          // set of map units, so there is nothing honest to put here.
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
            // No cell was described, so there is no coverage gap to report on top of the
            // provider fault: `availability: "unavailable"` is what the client captions
            // this view with, and a second "partly backfilled" note would compete with it.
            coverage: { cells: 0, covered: 0, ingested: 0 },
          };
        }
        rethrowUpstreamFault(error, "USDA Soil Data Access");
      }
    }),

  /**
   * One ERA5-Land soil field -- volumetric water or temperature -- for the viewport, on the
   * slider's day, at one depth.
   *
   * One procedure carrying `measure` rather than two: the two measures share every input,
   * every bound and every cache rule, and `measure` is part of the query key either way. A
   * second procedure would be a second place for the depth enum and the zoom contract to
   * drift.
   *
   * Deliberately NOT wrapped in `areaBoundedBbox`: like `getVegetationIndex` this reads the
   * local warehouse rather than proxying a third party, and zooming OUT is exactly when it
   * gets cheaper -- `zoom` moves it onto a coarser aggregation lattice, so a whole-PNW
   * request returns ~28 lattice nodes and at most nine isobands rather than 1,568 squares.
   * See `environmental-read-model.ts` §soil-field.
   */
  /**
   * The soil raster archives that are actually published, with the ramp their tiles were
   * painted with. Returns an empty array when nothing is published, which is the honest
   * answer and the one the layer tree renders as an inert row rather than a broken source.
   */
  getPublishedSoilRasters: publicProcedure.query(() =>
    getPublishedRasters("soilgrids", "pmtiles")
  ),

  getSoilField: publicProcedure
    .input(
      z.object({
        bbox: bboxSchema,
        date: observationDateSchema.optional(),
        // Enumerated from the shared tables rather than restated, so a measure or a depth
        // added there cannot be rejected here. The depth enum is the UNION across measures;
        // the reader resolves a depth the chosen measure does not publish to that measure's
        // first layer rather than querying a signal that cannot exist.
        measure: z.enum(SOIL_FIELD_MEASURE_IDS as [string, ...string[]]).optional(),
        depth: z.enum(SOIL_FIELD_DEPTHS as [string, ...string[]]).optional(),
        /** Viewport zoom; selects the aggregation tier. */
        zoom: z.number().finite().optional(),
      })
    )
    .query(async ({ input, signal }) => {
      try {
        return await getParquetSoilField(input.bbox, {
          date: input.date,
          measure: input.measure as SoilFieldMeasure | undefined,
          depth: input.depth as SoilFieldDepth | undefined,
          zoom: input.zoom,
          signal,
        });
      } catch (error) {
        const failure = parquetUpstreamFailure(error);
        if (failure !== null) {
          // Same split `rejectAborted` makes on the enveloping readers: this one throws its faults
          // rather than returning them, so the abort has to be separated here instead.
          throw new TRPCError({
            code: failure.fault.kind === "aborted" ? "CLIENT_CLOSED_REQUEST" : "SERVICE_UNAVAILABLE",
            message: failure.fault.message,
          });
        }
        throw error;
      }
    }),

  /**
   * One NASA POWER climate field for the viewport, on the slider's day, at the rung that serves
   * the caller's zoom.
   *
   * `zoom` is REQUIRED, like `getStreamflow`'s and unlike `getSoilField`'s optional one, because
   * there is no zoomless behaviour to preserve here: the reader this replaced pinned z13 for every
   * request. The claim that used to sit in this comment -- "this lane has one serving tier" -- was
   * false as written: the climate lanes publish z13/z9/z5/z0 like every other lane, and pinning the
   * detail rung meant the three coarse ones were written and never once read.
   *
   * Exactly ONE physical rung answers each request. `zoom` is therefore part of the query key, and
   * the map and the panel must pass the same one or they split into two cache entries drawing two
   * different aggregations of the same viewport.
   *
   * Deliberately NOT wrapped in `areaBoundedBbox`, for the same reason `getSoilField` is not:
   * it reads the local warehouse rather than proxying a third party, and the whole-lattice
   * answer is bounded by the lattice itself rather than by the viewport.
   */
  getClimateField: publicProcedure
    .input(
      z.object({
        bbox: bboxSchema,
        date: observationDateSchema.optional(),
        /** Viewport zoom; selects the one physical rung that answers. */
        zoom: mapZoomSchema,
        // Enumerated from the shared tables rather than restated, so a signal added there
        // cannot be rejected here. `variant` is the union across signals; the reader resolves
        // a variant the chosen signal does not publish to that signal's single reading.
        signal: z.enum(CLIMATE_FIELD_SIGNAL_IDS as [string, ...string[]]).optional(),
        variant: z.enum(AIR_TEMPERATURE_VARIANT_IDS as [string, ...string[]]).optional(),
        // The union of every form, not the chosen signal's own list. Narrowing it here would
        // put the per-signal rule -- no contour across precipitation, none across the pilot --
        // in a second place, and a schema rejection would surface as a failed request rather
        // than as the drawn default `resolveClimateRenderForm` degrades to.
        renderForm: z.enum(CLIMATE_RENDER_FORMS as [string, ...string[]]).optional(),
      })
    )
    // `abortSignal` is the cancellation and `signal` is the measured quantity; the reader's input
    // type spells the difference out so the two can never be handed to each other.
    .query(async ({ input, signal: abortSignal }) => {
      const signal =
        (input.signal as ClimateFieldSignalId | undefined) ?? DEFAULT_CLIMATE_FIELD_SIGNAL;
      const variant =
        (input.variant as AirTemperatureVariant | undefined) ??
        DEFAULT_AIR_TEMPERATURE_VARIANT;
      const renderForm = input.renderForm as ClimateRenderForm | undefined;
      const { zoomTier, result } = await getParquetClimateField({
        bbox: input.bbox,
        date: input.date,
        mapZoom: input.zoom,
        signal,
        variant,
        abortSignal,
      });
      if (result.state === "upstream_unavailable") {
        throw new TRPCError({
          code:
            result.fault.kind === "aborted" ? "CLIENT_CLOSED_REQUEST" : "SERVICE_UNAVAILABLE",
          message: result.fault.message,
        });
      }
      return parquetClimateFieldCollection(
        result,
        signal,
        variant,
        input.bbox,
        zoomTier,
        renderForm
      );
    }),

  getInterventionSuitability: publicProcedure
    .input(pointSchema)
    .query(({ input }) => getInterventionSuitability(input.lat, input.lon)),

  /**
   * What the time slider may offer, and the server's UTC today.
   * The client must take "today" from here and never from its own clock, or a
   * browser in another timezone silently disagrees about which days are future.
   */
  getSliderCapabilities: publicProcedure.query(() => getParquetSliderCapabilities()),

  /** Exact-day reads for the two explicitly PostgreSQL-owned fire-perimeter metrics. */
  getMetricAtDate: publicProcedure
    .input(
      z.object({
        metric: z.enum(METRIC_AT_DATE_IDS),
        // Required here, unlike the viewport reads above: this procedure exists to answer
        // "what did this metric read on this day", so there is no live-edge default.
        date: observationDateSchema,
        variant: z.enum(["observed", "monte_carlo", "ml"]).default("observed"),
        bbox: bboxSchema.optional(),
      })
    )
    .query(async ({ input }) => {
      const data = await getMetricAtDate(input);
      return {
        state: "ready" as const,
        requestedDay: input.date,
        servedDay: input.date,
        data,
        truncated: false,
      };
    }),
});
