/**
 * The governed Parquet readers behind every environmental tRPC procedure, one module per layer.
 *
 * This file is the public surface only; each reader lives in `parquet-trpc-readers/<layer>.ts`
 * and the cross-lane result/day/support vocabulary lives in `parquet-trpc-readers/shared.ts`.
 * Rationale: `src/lib/server/services/AGENTS.md` §parquet-trpc-readers.
 */

export {
  parquetUpstreamFailure,
  rejectAborted,
  type ParquetPolygonGeometry,
  type ParquetReaderFailureKind,
  type ParquetReaderResult,
  type ParquetViewportRead,
} from "./parquet-trpc-readers/shared";

export {
  getParquetWaterGauges,
  type ParquetWaterGauge,
} from "./parquet-trpc-readers/water-gauges";

export {
  getParquetWeatherObservations,
  type ParquetWeatherObservation,
} from "./parquet-trpc-readers/weather-observations";

export {
  getParquetClimateField,
  isParquetClimateFieldSignal,
  PARQUET_CLIMATE_FIELD_SIGNAL_IDS,
  type ParquetClimateFieldObservation,
  type ParquetClimateFieldRead,
  type ParquetClimateFieldSignalId,
} from "./parquet-trpc-readers/climate-field";

export {
  getParquetSoilField,
  type ZoomedSoilFieldCollection,
} from "./parquet-trpc-readers/soil-field";

export { getParquetDrought, type ParquetDroughtArea } from "./parquet-trpc-readers/drought";

export {
  getParquetEvacuationZones,
  type ParquetEvacuationZone,
} from "./parquet-trpc-readers/evacuation-zones";

export {
  getParquetFirePerimeters,
  type ParquetFirePerimeter,
} from "./parquet-trpc-readers/fire-perimeters";

export { getParquetWatersheds, type ParquetWatershed } from "./parquet-trpc-readers/watersheds";

export {
  getParquetBurnSeverity,
  type ParquetBurnScar,
} from "./parquet-trpc-readers/burn-severity";

export {
  getParquetSensorStations,
  type ParquetSensorStation,
} from "./parquet-trpc-readers/sensors";

export {
  getParquetVegetation,
  VEGETATION_TRAILING_DAYS,
  type ParquetVegetationObservation,
  type ParquetVegetationWindow,
} from "./parquet-trpc-readers/vegetation";

export {
  getParquetFireDetections,
  type ParquetFireDetectionCell,
  type ParquetFireWindow,
} from "./parquet-trpc-readers/fire-detections";
