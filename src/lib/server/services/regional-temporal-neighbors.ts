import { TRPCError } from "@trpc/server";
import { haversineDistance } from "@/lib/map/measurement";
import type { ResolvedSliderCapabilities, ResolvedSliderLayerCapability } from "./environmental-read-model";
import {
  getParquetFireDetections,
  getParquetWaterGauges,
  getParquetWeatherObservations,
  parquetUpstreamFailure,
  rejectAborted,
  type ParquetFireDetectionCell,
  type ParquetReaderResult,
  type ParquetWaterGauge,
  type ParquetWeatherObservation,
} from "./parquet-trpc-readers";

const WINDOW_DAYS = 180;
const DAY_MS = 86_400_000;
type Source = "fire-detections" | "water-gauges" | "weather-observations";
type ObservationRow = ParquetFireDetectionCell | ParquetWaterGauge | ParquetWeatherObservation;
type Read = ParquetReaderResult<readonly ObservationRow[]>;
type ReadStatus = Exclude<Read, { state: "ready" }> | (Omit<Extract<Read, { state: "ready" }>, "data"> & { rowCount: number });

const ALIASES: Readonly<Record<string, string>> = {
  fire: "fire-detections", fireDetections: "fire-detections", "fire-detections": "fire-detections",
  water: "water-gauges", streamflow: "water-gauges", "water-gauges": "water-gauges",
  weather: "weather-observations", weatherObservations: "weather-observations", "weather-observations": "weather-observations",
  drought: "drought", "drought-areas": "drought",
  mtbsPerimeters: "burn-severity", "burn-severity": "burn-severity",
  firePerimeters: "fire-perimeters", "fire-perimeters": "fire-perimeters",
  evacuationZones: "evacuation-zones", "evacuation-zones": "evacuation-zones",
  soilSurvey: "soil-survey", "soil-survey": "soil-survey",
};

export interface RegionalTemporalNeighbourCandidate {
  candidateDay: string | null;
  state: "observed" | "no_local_observation" | "no_published_candidate" | "refused" | "truncated"
    | "absent" | "not_generated" | "upstream_unavailable";
  reason: string | null;
  read: ReadStatus | null;
  observation: {
    value: ObservationRow;
    observedDay: string;
    dayOffset: number;
    distanceDays: number;
    distanceMeters: number;
    searchBbox: string;
  } | null;
}

export interface RegionalTemporalNeighbour {
  source: string;
  viewedLayers: string[];
  viewedDays: string[];
  searchBbox: string;
  searchWindowDays: number;
  describedFromDay: string | null;
  describedThroughDay: string | null;
  state: "answered" | "refused" | "unsupported";
  reason: string | null;
  before: RegionalTemporalNeighbourCandidate | null;
  after: RegionalTemporalNeighbourCandidate | null;
}

export interface RegionalTemporalNeighbourInput {
  viewedLayers: readonly { layer: string; date: string }[];
  capabilities: ResolvedSliderCapabilities | null;
  lat: number;
  lon: number;
  bbox: string;
  signal?: AbortSignal;
}

function calendarDay(value: unknown): value is string {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const instant = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(instant.getTime()) && instant.toISOString().slice(0, 10) === value;
}

function shifted(day: string, offset: number): string {
  return new Date(Date.parse(`${day}T00:00:00Z`) + offset * DAY_MS).toISOString().slice(0, 10);
}

function supported(source: string): source is Source {
  return source === "fire-detections" || source === "water-gauges" || source === "weather-observations";
}

function rejectCancellation(signal: AbortSignal | undefined): void {
  if (signal?.aborted) throw new TRPCError({ code: "CLIENT_CLOSED_REQUEST", message: "Temporal neighbour read cancelled." });
}

function bounds(capability: ResolvedSliderLayerCapability, today: string): { from: string; through: string } | null {
  if (capability.coverageAuthority !== "availability" || !calendarDay(today)
    || !calendarDay(capability.earliestObservedDate) || !calendarDay(capability.latestObservedDate)
    || !calendarDay(capability.describedThroughDay)
    || (capability.describedFromDay !== null && !calendarDay(capability.describedFromDay))
    || (capability.coverageGapsDescribedFromDay !== null && !calendarDay(capability.coverageGapsDescribedFromDay))
    || (capability.coverageGapsTruncated && !calendarDay(capability.coverageGapsDescribedFromDay))
    || (capability.sourceCeilingDay != null && !calendarDay(capability.sourceCeilingDay))) return null;
  const ranges = [...capability.coverageGaps, ...(capability.governedAbsenceRanges ?? [])];
  if (ranges.some((range) => !calendarDay(range.from) || !calendarDay(range.to) || range.from > range.to)) return null;
  const from = [capability.earliestObservedDate, capability.describedFromDay, capability.coverageGapsDescribedFromDay]
    .filter((day): day is string => day !== null).sort().at(-1)!;
  const through = [capability.latestObservedDate, capability.describedThroughDay, capability.sourceCeilingDay ?? today, today].sort()[0];
  return from <= through ? { from, through } : null;
}

function nearestPublishedDay(capability: ResolvedSliderLayerCapability, described: { from: string; through: string }, viewedDay: string, direction: -1 | 1): string | null {
  const excluded = [...capability.coverageGaps, ...(capability.governedAbsenceRanges ?? [])];
  for (let offset = 1; offset <= WINDOW_DAYS; offset += 1) {
    const day = shifted(viewedDay, direction * offset);
    if (day < described.from || day > described.through) continue;
    if (!excluded.some((range) => range.from <= day && day <= range.to)) return day;
  }
  return null;
}

function viewport(input: RegionalTemporalNeighbourInput): [number, number, number, number] | null {
  const components = input.bbox.split(",");
  const values = components.map(Number);
  if (!Number.isFinite(input.lat) || !Number.isFinite(input.lon) || Math.abs(input.lat) > 90 || Math.abs(input.lon) > 180
    || values.length !== 4 || components.some((value) => value.trim() === "") || values.some((value) => !Number.isFinite(value))) return null;
  const [west, south, east, north] = values;
  return west >= -180 && east <= 180 && south >= -90 && north <= 90 && west < east && south < north
    ? [west, south, east, north] : null;
}

async function readCandidate(source: Source, day: string, input: RegionalTemporalNeighbourInput): Promise<Read> {
  const request = { bbox: input.bbox, date: day, mapZoom: 13, signal: input.signal };
  if (source === "water-gauges") return getParquetWaterGauges(request);
  if (source === "weather-observations") return getParquetWeatherObservations(request);
  const result = rejectAborted(await getParquetFireDetections(request));
  if (result.state !== "ready") return result;
  if (result.requestedDay !== day || result.servedDay !== day || result.data.firstDay !== day || result.data.lastDay !== day || result.data.days.length !== 1) {
    return { state: "upstream_unavailable", fault: { kind: "contract", message: "Fire neighbour did not answer exactly one candidate day." } };
  }
  const member = rejectAborted(result.data.days[0]);
  return member.state === "ready" ? { ...member, truncated: result.truncated || member.truncated } : member;
}

function candidate(candidateDay: string | null, state: RegionalTemporalNeighbourCandidate["state"], reason: string | null, read: ReadStatus | null = null): RegionalTemporalNeighbourCandidate {
  return { candidateDay, state, reason, read, observation: null };
}

async function resolveCandidate(source: Source, day: string | null, viewedDay: string, input: RegionalTemporalNeighbourInput, box: [number, number, number, number]): Promise<RegionalTemporalNeighbourCandidate> {
  rejectCancellation(input.signal);
  if (day === null) return candidate(null, "no_published_candidate", "No globally published candidate in the described bounds within 180 calendar days on this side.");
  let result: Read;
  try {
    result = rejectAborted(await readCandidate(source, day, input));
    rejectCancellation(input.signal);
  } catch (error) {
    rejectCancellation(input.signal);
    if (error instanceof TRPCError && error.code === "CLIENT_CLOSED_REQUEST") throw error;
    const fault = parquetUpstreamFailure(error);
    result = fault === null ? { state: "upstream_unavailable", fault: { kind: "contract", message: "Temporal neighbour reader could not establish a bounded answer." } } : rejectAborted(fault);
  }
  if (result.state === "upstream_unavailable") return candidate(day, result.state, result.fault.message, result);
  if (result.requestedDay !== day || (result.state !== "not_generated" && result.servedDay !== day)) {
    return candidate(day, "refused", "The exact-day neighbour envelope answered a different day.");
  }
  if (result.state === "not_generated") return candidate(day, result.state, result.reason, result);
  if (result.state === "absent") return candidate(day, result.state, "The candidate now has a governed absence; its evidence is retained.", result);
  const status: ReadStatus = { state: result.state, requestedDay: result.requestedDay, servedDay: result.servedDay, truncated: result.truncated, rowCount: result.data.length };
  if (result.truncated) return candidate(day, "truncated", "The candidate read was truncated; nearest local evidence cannot be established.", status);
  let nearest: { row: ObservationRow; distance: number } | null = null;
  const [west, south, east, north] = box;
  for (const row of result.data) {
    const { latitude, longitude } = row;
    if (row.observedDay !== day || latitude === null || longitude === null || !Number.isFinite(latitude) || !Number.isFinite(longitude)
      || latitude < south || latitude > north || longitude < west || longitude > east || row.support.zoomTier !== 13
      || (source === "water-gauges" && (!("siteNumber" in row) || !row.siteNumber || !row.siteName))) {
      return candidate(day, "refused", "A candidate row lacks exact-day detail identity or lies outside the requested viewport.", status);
    }
    const distance = haversineDistance([input.lon, input.lat], [longitude, latitude]);
    if (!Number.isFinite(distance)) return candidate(day, "refused", "The candidate's spatial distance could not be established.", status);
    if (nearest === null || distance < nearest.distance) nearest = { row, distance };
  }
  if (nearest === null) return candidate(day, "no_local_observation", "No local observation on nearest globally published day; other days were not scanned.", status);
  const dayOffset = (Date.parse(`${nearest.row.observedDay}T00:00:00Z`) - Date.parse(`${viewedDay}T00:00:00Z`)) / DAY_MS;
  return {
    ...candidate(day, "observed", null, status),
    observation: { value: nearest.row, observedDay: nearest.row.observedDay, dayOffset, distanceDays: Math.abs(dayOffset), distanceMeters: nearest.distance, searchBbox: input.bbox },
  };
}

/** Fetch at most one globally published candidate per side for each of three daily sources. */
export async function getRegionalTemporalNeighbours(input: RegionalTemporalNeighbourInput): Promise<RegionalTemporalNeighbour[]> {
  rejectCancellation(input.signal);
  const groups = new Map<string, { layers: Set<string>; days: Set<string> }>();
  for (const row of input.viewedLayers) {
    const source = Object.hasOwn(ALIASES, row.layer) ? ALIASES[row.layer] : row.layer;
    const group = groups.get(source) ?? { layers: new Set<string>(), days: new Set<string>() };
    group.layers.add(row.layer);
    group.days.add(row.date);
    groups.set(source, group);
  }
  const records: RegionalTemporalNeighbour[] = [];
  const jobs: (() => Promise<void>)[] = [];
  const box = viewport(input);
  for (const [source, group] of groups) {
    rejectCancellation(input.signal);
    const days = [...group.days].sort();
    const record: RegionalTemporalNeighbour = {
      source, viewedLayers: [...group.layers], viewedDays: days, searchBbox: input.bbox, searchWindowDays: WINDOW_DAYS,
      describedFromDay: null, describedThroughDay: null, state: "refused", reason: null, before: null, after: null,
    };
    records.push(record);
    if (!supported(source)) {
      record.state = "unsupported";
      record.reason = "Temporal value neighbours are supported only for daily fire, gauge and weather readers; release carry, static and non-Parquet dates do not prove release neighbours.";
      continue;
    }
    if (days.length !== 1 || !calendarDay(days[0])) { record.reason = "Conflicting or invalid viewed days for aliases of the same source."; continue; }
    if (box === null) { record.reason = "A finite geographic point and bounded viewport are required."; continue; }
    const payload = input.capabilities;
    if (payload === null || payload.streamsUnavailable || ("parquetCoverageUnavailable" in payload && payload.parquetCoverageUnavailable === true)) {
      record.reason = "Availability capability evidence is unavailable.";
      continue;
    }
    if ("withheldParquetCapabilities" in payload && Array.isArray(payload.withheldParquetCapabilities)
      && payload.withheldParquetCapabilities.some((entry: unknown) => entry !== null && typeof entry === "object" && "layerName" in entry && entry.layerName === source)) {
      record.reason = "The source capability is explicitly withheld.";
      continue;
    }
    const capabilities = payload.layers.filter((layer) => layer.layerName === source);
    if (capabilities.length !== 1) { record.reason = "The source has missing or conflicting capability evidence."; continue; }
    const capability = capabilities[0];
    const described = bounds(capability, payload.serverCurrentDate);
    if (described === null) { record.reason = "Availability authority and complete described day bounds are required."; continue; }
    record.describedFromDay = described.from;
    record.describedThroughDay = described.through;
    jobs.push(async () => {
      record.before = await resolveCandidate(source, nearestPublishedDay(capability, described, days[0], -1), days[0], input, box);
      record.after = await resolveCandidate(source, nearestPublishedDay(capability, described, days[0], 1), days[0], input, box);
      record.state = "answered";
    });
  }
  await Promise.all(jobs.map((job) => job()));
  return records;
}
