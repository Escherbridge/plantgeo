/**
 * The ONE switch naming the Parquet stream the `water-gauges` layer is served from (config-driven
 * ingestion spec §7a). The UI layer key stays `water-gauges`; only the stream behind it moves.
 *
 * G4 (owner go, after every validation row passes) flips `WATER_GAUGES_STREAM` to
 * `DAILY_WATER_GAUGES_STREAM` in the same commit as the agent's
 * `services/agri-data-service/src/agri_data_service/agent/surfaces.py::WATER_GAUGES_SERVED_STREAM`.
 * Rollback is the same line back. See `docs/lanes/water-gauges.md` "The web switch".
 */

export const LEGACY_WATER_GAUGES_STREAM = "water-gauges";
export const DAILY_WATER_GAUGES_STREAM = "water-gauges-daily";

export type WaterGaugesStream = typeof LEGACY_WATER_GAUGES_STREAM | typeof DAILY_WATER_GAUGES_STREAM;

/** The stream every water reader, capability, attribution and alert names today. */
export const WATER_GAUGES_STREAM: WaterGaugesStream = LEGACY_WATER_GAUGES_STREAM;

export interface WaterGaugesStreamFacts {
  /** Shown wherever the stream's values are drawn (`parquet-trpc-readers/shared.ts::LANE_ATTRIBUTIONS`). */
  attribution: string;
  /** The about page's source term, description and cadence note. */
  sourceTerm: string;
  sourceDescription: string;
  sourceCadence: string;
  /** The slider capability's `selectableHistoryFloor` for this stream. */
  selectableHistoryFloor: string;
  /** How one gauge's number is labelled: an instantaneous reading, or a daily mean. */
  readingLabel: string;
  readingUnit: string;
  /** Only an instantaneous reading has a measurement instant worth printing; a daily mean names a day. */
  showsMeasuredInstant: boolean;
}

export const WATER_GAUGES_STREAM_FACTS = {
  [LEGACY_WATER_GAUGES_STREAM]: {
    attribution: "U.S. Geological Survey NWIS",
    sourceTerm: "USGS NWIS",
    sourceDescription: "Instantaneous streamflow discharge from active stream gauges.",
    sourceCadence: "Hourly",
    selectableHistoryFloor: "2022-08-05",
    readingLabel: "Discharge",
    readingUnit: "cfs",
    showsMeasuredInstant: true,
  },
  [DAILY_WATER_GAUGES_STREAM]: {
    attribution: "U.S. Geological Survey Water Data daily values",
    sourceTerm: "USGS Water Data",
    sourceDescription:
      "Daily mean streamflow discharge from stream gauges, on the day each gauge's own record names.",
    sourceCadence: "Daily",
    selectableHistoryFloor: "1990-09-30",
    readingLabel: "Daily mean discharge",
    readingUnit: "cfs daily mean",
    showsMeasuredInstant: false,
  },
} as const satisfies Readonly<Record<WaterGaugesStream, WaterGaugesStreamFacts>>;

/** The facts of the stream `WATER_GAUGES_STREAM` names. */
export const WATER_GAUGES_STREAM_FACT: WaterGaugesStreamFacts = WATER_GAUGES_STREAM_FACTS[WATER_GAUGES_STREAM];
