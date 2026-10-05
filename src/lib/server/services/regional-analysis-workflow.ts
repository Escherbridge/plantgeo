import {
  isStrategyKnowledgeTool,
  LITERATURE_EVIDENCE_DOMAIN,
  REGIONAL_TOOL_EVIDENCE_SOURCES,
  STRATEGY_KNOWLEDGE_EVIDENCE_SOURCE,
  type ConversationTurn,
  type RegionalAnalysisEvidence,
} from '@/lib/regional-intelligence';
import { buildRegionalMeasurementFacts, type RegionalMeasurementFacts } from './regional-measurement-facts';
import { isLayerToggleId, LAYER_REGISTRY } from '@/lib/map/layer-registry';
import { analysisDateRange, DEFAULT_ANALYSIS_WINDOW, isAnalysisCalendarDay } from '@/lib/regional-analysis-selection';
import { UpstreamHttpError } from '@/lib/server/http/bounded-upstream';
import { selectionTile } from './regional-map-evidence';
import { resolveZoomTier } from '@/lib/map/zoom-tiers';
import type { RegionalContextPayload, TemporalContext } from './regional-context';
import {
  callRegionalEvidenceTool,
  loadRegionalEvidenceTools,
  type LiteratureServerContext,
  type LiteratureSiteFacts,
  type RegionalEvidenceCatalogue,
} from './regional-evidence-tools';

type AuditCall = RegionalAnalysisEvidence['toolCalls'][number];
type ReadRequest = { tool: string; source?: string; staticLayer?: boolean; args: Record<string, unknown> };
type EvidenceRead = { id: string; evidenceReadId: string; evidenceSource?: string; evidenceStatus: AuditCall['status']; result: unknown };

export const REGIONAL_ANALYSIS_STAGES = [
  ['inventory', 'Discover environmental sources'],
  ['local', 'Read local conditions'],
  ['temporal', 'Compare historical conditions'],
  ['strategies', 'Screen management alternatives'],
] as const;

/**
 * Initial reads: every theme reads its anchors; a fallback stands in only for an anchor the
 * deployed catalogue lacks. See services/AGENTS.md §regional-analysis-themes.
 */
export const REGIONAL_ANALYSIS_THEMES = [
  { theme: 'fire', anchors: ['fire-detections', 'fire-perimeters'], fallbacks: ['burn-severity'] },
  { theme: 'drought', anchors: ['drought-areas'], fallbacks: [] },
  { theme: 'weather', anchors: ['weather-observations'], fallbacks: ['climate-field-air-temperature'] },
  { theme: 'water', anchors: ['water-gauges'], fallbacks: ['watersheds', 'groundwater'] },
  { theme: 'soil', anchors: ['soil-survey'], fallbacks: ['soil-field-moisture'] },
  { theme: 'vegetation', anchors: ['vegetation'], fallbacks: [] },
  { theme: 'climate', anchors: ['climate-field-precipitation'], fallbacks: ['soil-field-vpd', 'climate-field-soil-wetness-root-zone'] },
] as const;

/** The user's own dated or visible layers read beside the theme anchors. */
export const MAX_SELECTED_INITIAL_READS = 4;

/** `static_lookup` surfaces: a version, no date axis, so one read at the current release and no history. */
export const REGIONAL_STATIC_SURFACES: ReadonlySet<string> = new Set([
  'soil-survey', 'fire-perimeters', 'evacuation-zones', 'watersheds', 'land-context-boundaries',
  'soil-phh2o', 'soil-soc', 'soil-nitrogen', 'soil-bdod', 'soil-cec', 'soil-ocd',
]);

/** Forecast surfaces legitimately serve future days, so their window is not capped at today. */
const FORECAST_SURFACES: ReadonlySet<string> = new Set(['weather-forecast', 'fire-risk']);

/** Concurrent reads per stage; the map's own tile reads share the same serving slots. */
const STAGE_CONCURRENCY = 2;
/** Process-state refusals an identical retry can answer differently (agri llm.py `_TRANSIENT_SERVING_REFUSAL_CODES`). */
const TRANSIENT_REFUSAL_CODES: ReadonlySet<string> = new Set(['serving_at_capacity', 'release_read_changed']);
const TRANSIENT_HTTP_STATUSES: ReadonlySet<number> = new Set([429, 502, 503]);
/** Bridge 503s that are deterministic for the same arguments, so never retried. */
const NON_TRANSIENT_BRIDGE_CODES = ['tool_response_too_large', 'tool_read_timeout'];

/**
 * Plan the initial surfaces: the user's own selected layers FIRST (any theme anchor among them, plus
 * up to four others), then every theme anchor or stand-in not already planned, themes interleaved.
 * The set is unchanged by the order; only who reads first inside the 12 s local stage is.
 */
export function regionalInitialSurfaces(selected: readonly string[], available: readonly string[]): string[] {
  const offered = new Set(available);
  const anchors = REGIONAL_ANALYSIS_THEMES.map(({ anchors, fallbacks }) => {
    const present: string[] = anchors.filter((surface) => offered.has(surface));
    const replacements = fallbacks.filter((surface) => offered.has(surface));
    return [...present, ...replacements.slice(0, anchors.length - present.length)];
  });
  const themeReads: string[] = [];
  for (let slot = 0; slot < Math.max(...anchors.map((entry) => entry.length)); slot += 1) {
    for (const entry of anchors) if (entry[slot] && !themeReads.includes(entry[slot])) themeReads.push(entry[slot]);
  }
  const planned: string[] = [];
  let extras = 0;
  for (const surface of new Set(selected)) {
    if (!offered.has(surface)) continue;
    if (themeReads.includes(surface)) planned.push(surface);
    else if (extras < MAX_SELECTED_INITIAL_READS) {
      planned.push(surface);
      extras += 1;
    }
  }
  return [...planned, ...themeReads.filter((surface) => !planned.includes(surface))];
}

export function isRegionalStaticSurface(source: string): boolean {
  return REGIONAL_STATIC_SURFACES.has(source);
}

const SOURCE_SURFACE: Record<string, string> = {
  drought: 'drought-areas', streamflow: 'water-gauges', weatherObservations: 'weather-observations',
  fireDetections: 'fire-detections', firePerimeters: 'fire-perimeters', mtbsPerimeters: 'burn-severity',
};

export function regionalSurfaceName(layer: string): string {
  return isLayerToggleId(layer) ? LAYER_REGISTRY[layer].warehouseLayerName ?? layer : layer;
}

export function regionalEvidenceDay(temporal: TemporalContext, source: string): string {
  if (source === 'crop-cover') return temporal.analysisSelection?.cropCoverReleaseDay ?? temporal.serverCurrentDate;
  const selected = Object.entries(temporal.analysisSelection?.layerDays ?? {})
    .find(([layer]) => regionalSurfaceName(layer) === source)?.[1];
  if (selected) return selected;
  return temporal.readings.find((row) => row.layer === source
    || (isLayerToggleId(row.layer) && LAYER_REGISTRY[row.layer].warehouseLayerName === source)
    || SOURCE_SURFACE[row.evidenceSource ?? ''] === source)?.viewedDate
    ?? Object.values(temporal.analysisSelection?.layerDays ?? {}).sort().at(-1)
    ?? temporal.viewedDates.at(-1) ?? temporal.serverCurrentDate;
}

/** This layer's own window from `layerWindows`, widened to contain `day`; null when absent or malformed. */
function regionalLayerWindow(temporal: TemporalContext, source: string, day: string) {
  const window = Object.entries(temporal.analysisSelection?.layerWindows ?? {})
    .find(([layer]) => regionalSurfaceName(layer) === source)?.[1];
  if (!window || !isAnalysisCalendarDay(window.rangeStart) || !isAnalysisCalendarDay(window.rangeEnd)
    || window.rangeStart > window.rangeEnd) return null;
  return {
    rangeStart: window.rangeStart < day ? window.rangeStart : day,
    rangeEnd: window.rangeEnd > day ? window.rangeEnd : day,
  };
}

/**
 * Bind numeric tile reads to this request's location and calendar selection. History reads use the
 * layer's own window, else the trailing global window; neither extends past the server's UTC today
 * (forecast surfaces excepted). A non-history read is one exact day at day scale.
 */
export function regionalSelectionArguments(
  payload: RegionalContextPayload, temporal: TemporalContext, source: string, history = true,
): Record<string, unknown> {
  const today = temporal.serverCurrentDate;
  const capped = !FORECAST_SURFACES.has(source) && isAnalysisCalendarDay(today);
  const requested = regionalEvidenceDay(temporal, source);
  const day = capped && isAnalysisCalendarDay(requested) && requested > today ? today : requested;
  const window = temporal.analysisSelection ?? DEFAULT_ANALYSIS_WINDOW;
  const layerWindow = history ? regionalLayerWindow(temporal, source, day) : null;
  const range = !history ? { rangeStart: day, rangeEnd: day }
    : layerWindow ? { rangeStart: layerWindow.rangeStart, rangeEnd: capped && layerWindow.rangeEnd > today ? today : layerWindow.rangeEnd }
      : analysisDateRange(day, window.timeScale, window.rangeSteps, capped ? today : undefined);
  return {
    surface_name: source, day,
    longitude: payload.location.lon, latitude: payload.location.lat,
    range_start: range.rangeStart, range_end: range.rangeEnd,
    time_scale: history ? window.timeScale : 'day', zoom: temporal.analysisSelection?.zoom ?? 13,
  };
}

/** Literature arguments the server owns (seam S1); the model's copies never reach the bridge. */
export const SERVER_OWNED_LITERATURE_ARGUMENTS = ['site_profile', 'region'] as const;

export function bindRegionalEvidenceArguments(
  tool: string, args: Record<string, unknown>, payload: RegionalContextPayload, temporal: TemporalContext,
): Record<string, unknown> {
  // Literature arguments stay coordinate-free: the point travels only in `server_context`, from which
  // the agri bridge derives the region and site facts, so the model's site_profile and region are dropped.
  if (isStrategyKnowledgeTool(tool)) {
    return Object.fromEntries(Object.entries(args)
      .filter(([key]) => !(SERVER_OWNED_LITERATURE_ARGUMENTS as readonly string[]).includes(key)));
  }
  const source = typeof args.surface_name === 'string' ? regionalSurfaceName(args.surface_name)
    : tool === 'drought_history_at_point' ? 'drought-areas' : tool === 'fire_history_near_point' ? 'burn-severity' : '';
  if (tool === 'surface_evidence_for_selection') {
    return { ...args, ...regionalSelectionArguments(payload, temporal, source, !isRegionalStaticSurface(source)) };
  }
  const day = regionalEvidenceDay(temporal, source);
  const tile = 'bbox' in args ? selectionTile(payload.location.lon, payload.location.lat, temporal.analysisSelection?.zoom ?? 13) : null;
  return {
    ...args,
    ...(tool === 'read_crop_cover_in_area' ? {
      asOfDay: temporal.analysisSelection?.cropCoverReleaseDay ?? temporal.serverCurrentDate,
      zoomTier: resolveZoomTier(temporal.analysisSelection?.zoom ?? 13),
    } : {}),
    ...(source && 'surface_name' in args ? { surface_name: source } : {}),
    ...('longitude' in args || 'latitude' in args ? { longitude: payload.location.lon, latitude: payload.location.lat } : {}),
    ...('lon' in args || 'lat' in args ? { lon: payload.location.lon, lat: payload.location.lat } : {}),
    ...('bbox' in args ? { bbox: tile ? {
      west: tile.bbox[0], south: tile.bbox[1], east: tile.bbox[2], north: tile.bbox[3],
    } : null } : {}),
    ...('day' in args ? { day } : {}),
    ...('as_of_day' in args || ['drought_history_at_point', 'fire_history_near_point'].includes(tool) ? { as_of_day: day } : {}),
  };
}

export const STRATEGY_SCREENING = [
  { strategy: 'silvopasture', requires: ['existing land use and restoration goals', 'compatible trees and forage', 'livestock access and rotational grazing capacity', 'water balance and tree regeneration'], source: 'https://research.fs.usda.gov/centers/nac/silvopasture' },
  { strategy: 'biochar', requires: ['soil test including pH and texture', 'feedstock and production conditions', 'product quality and contaminants', 'amendment goal and local application guidance'], source: 'https://www.climatehubs.usda.gov/hubs/northwest/topic/biochar' },
  { strategy: 'managed_grazing', requires: ['livestock and forage availability', 'seasonal carrying capacity', 'soil compaction and erosion risk', 'rest periods and riparian protection'] },
  { strategy: 'water_harvesting', requires: ['terrain and drainage', 'seasonal precipitation', 'soil infiltration', 'downstream water and local permitting'] },
  { strategy: 'keyline', requires: ['terrain and contour survey', 'soil infiltration and disturbance constraints', 'drainage and downstream water effects', 'local design suitability'] },
  { strategy: 'riparian_buffer', requires: ['watercourse location', 'bank condition', 'flooding regime', 'locally appropriate vegetation'] },
  { strategy: 'cover_cropping', requires: ['cropland or other compatible land use', 'seasonal soil moisture', 'establishment window', 'water competition'] },
  { strategy: 'reforestation', requires: ['historical ecosystem and land use', 'water availability', 'species suitability', 'establishment and maintenance capacity'] },
  { strategy: 'erosion_control', requires: ['slope and disturbed soil', 'runoff pathways', 'vegetative cover', 'sediment exposure'] },
  { strategy: 'fuel_reduction', requires: ['observed vegetation and fuel structure', 'assets and fire exposure', 'habitat constraints', 'treatment and maintenance capacity'] },
] as const;

function object(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown> : null;
}

/** Service-side literature refusals read as unavailable; any other literature error stays a refusal. */
const LITERATURE_UNAVAILABLE_CODES = new Set(['strategy_knowledge_not_configured', 'strategy_knowledge_unavailable']);

/** Whether a tool result is a strategy-knowledge literature payload, by tool name or its evidence domain. */
export function isLiteratureEvidence(tool: string | undefined, value: unknown): boolean {
  return (tool !== undefined && isStrategyKnowledgeTool(tool)) || object(value)?.evidence_domain === LITERATURE_EVIDENCE_DOMAIN;
}

/**
 * Literature is answered (with or without records) or refused, never observed: it reports cited
 * sources, not this site. A zero-record answer is `answered_no_records`, not `answered` --
 * `strategyKnowledgeAnswered` keys on the exact `answered` status, so a lookup that found nothing
 * cannot unlock the literature evidence origin while still reading as a real (non-failure) answer.
 */
function literatureResultStatus(root: Record<string, unknown>): Pick<AuditCall, 'status' | 'summary' | 'reason'> {
  const refusalState = typeof root.state === 'string' && (root.state === 'refused' || root.state.startsWith('strategy_knowledge_'))
    ? root.state : undefined;
  const code = typeof root.error === 'string' ? root.error
    : typeof root.refusal_code === 'string' ? root.refusal_code : refusalState;
  if (code !== undefined || root.error || root.refusal_code) {
    const detail = typeof root.refusal_detail === 'string' ? root.refusal_detail
      : typeof root.note === 'string' ? root.note : 'The literature lookup was refused.';
    return {
      status: code !== undefined && LITERATURE_UNAVAILABLE_CODES.has(code) ? 'unavailable' : 'refused',
      reason: `${code ?? 'strategy_knowledge_refused'}: ${detail}`.slice(0, 240),
    };
  }
  // Prefer the service's own `result_count` (agent/strategy_knowledge.py `ask`): it counts every
  // projection (results/strategies) the same way the service does. Fall back to the array-length
  // reduce for payloads that omit it (e.g. hand-built test fixtures).
  const records = typeof root.result_count === 'number' && Number.isFinite(root.result_count)
    ? root.result_count
    : ['results', 'strategies', 'findings']
      .reduce((total, key) => total + (Array.isArray(root[key]) ? (root[key] as unknown[]).length : 0), 0);
  const corpus = typeof root.corpus_version === 'string' ? ` (corpus ${root.corpus_version.slice(0, 64)})` : '';
  return records > 0 ? {
    status: 'answered',
    summary: `${records} strategy-knowledge literature records returned${corpus}. Cited sources report these; they are not measurements at this location.`,
  } : {
    status: 'answered_no_records',
    summary: `No matching strategy-knowledge literature record${corpus}. This is not evidence that no strategy exists.`,
  };
}

/** Classify evidence only from returned rows and explicit serving states. */
export function evidenceResultStatus(value: unknown, tool?: string): Pick<AuditCall, 'status' | 'summary' | 'reason'> {
  const root = object(value);
  if (!root) return { status: 'error', reason: 'The tool returned an invalid evidence object.' };
  if (isLiteratureEvidence(tool, root)) return literatureResultStatus(root);
  if (root.error || root.refusal_code) {
    // Keep the specific code beside a generic wrapper ("parquet_serving_refused: serving_at_capacity").
    const reason = root.error && typeof root.refusal_code === 'string' && root.refusal_code !== root.error
      ? `${String(root.error)}: ${root.refusal_code}` : String(root.error ?? root.refusal_code);
    return { status: 'refused', reason: reason.slice(0, 240) };
  }
  const states: string[] = [];
  let rows = 0;
  const visit = (entry: unknown, key = '') => {
    if (Array.isArray(entry)) {
      if (['features', 'rows', 'weekly_severity'].includes(key)) rows += entry.length;
      if (key === 'layer_summaries') rows += entry.filter((row) => Number(object(row)?.row_count ?? object(row)?.feature_count ?? 0) > 0).length;
      for (const child of entry) visit(child);
    } else if (object(entry)) {
      for (const [name, child] of Object.entries(entry as Record<string, unknown>)) {
        if (name === 'state' && typeof child === 'string') states.push(child);
        else visit(child, name);
      }
    }
  };
  visit(root);
  if (rows > 0) return { status: 'observed', summary: `${rows} returned measurement records; each retains its own date and spatial support. Missing days are not observations.` };
  if (states.some((state) => ['day_not_written', 'lane_never_written', 'not_published', 'coverage_unknown', 'not_generated', 'upstream_unavailable'].includes(state))) {
    return { status: 'unavailable', reason: 'At least one required partition or coverage state is unavailable; inspect the individual lane evidence.' };
  }
  if (states.length > 0 && states.every((state) => state === 'governed_absence')) {
    return { status: 'governed_absence', summary: 'The serving contract explicitly declares a governed absence.' };
  }
  return { status: 'unavailable', reason: typeof root.note === 'string' ? root.note.slice(0, 240) : 'No local observation rows were returned; this alone does not establish an absence.' };
}

const MODEL_OMITTED_STORAGE_LINEAGE = new Set([
  'selected_source_part_key', 'input_source_part_keys', 'selected_source_part_sha256',
  'input_source_part_sha256s', 'selected_source_row_sha256', 'input_source_row_sha256s',
  'input_source_row_digest', 'source_manifest_sha256', 'selected_source_release_payload_checksum',
]);

/** Matches the strategy-knowledge tools' own `limit` ceiling (1-10), so no requested record is cut. */
export const MAX_LITERATURE_RESULTS = 10;
const LITERATURE_RESULT_FIELDS = new Set(['results', 'strategies', 'findings']);

/** Keep measurement provenance while counting omitted rows and opaque storage lineage. */
export function boundedEvidence(value: unknown, depth = 0, field = '', literatureRoot = false): unknown {
  if (depth > 12) return { omitted: 'Nested detail exceeds the evidence display bound.' };
  if (typeof value === 'string') return value.length > 2_000 ? `${value.slice(0, 2_000)} [text truncated]` : value;
  if (Array.isArray(value)) {
    const limit = field === 'weekly_severity' ? 120 : ['history', 'sampled_days'].includes(field) ? 31
      : literatureRoot && LITERATURE_RESULT_FIELDS.has(field) ? MAX_LITERATURE_RESULTS : 8;
    const entries = value.slice(0, limit).map((entry) => boundedEvidence(entry, depth + 1));
    return value.length > limit ? { entries, omittedEntries: value.length - limit } : entries;
  }
  if (object(value)) {
    const entries = Object.entries(value as Record<string, unknown>);
    const retained = entries.filter(([key]) => !MODEL_OMITTED_STORAGE_LINEAGE.has(key));
    // Only the top-level record arrays of a literature payload get the wider bound.
    const literature = depth === 0 && isLiteratureEvidence(undefined, value);
    return {
      ...Object.fromEntries(retained.map(([key, entry]) => [key, boundedEvidence(entry, depth + 1, key, literature)])),
      ...(retained.length < entries.length ? { omittedStorageLineageFields: entries.length - retained.length } : {}),
    };
  }
  return value;
}

function isCalendarDay(value: unknown): value is string {
  return typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value)
    && Number.isFinite(Date.parse(`${value}T00:00:00Z`))
    && new Date(`${value}T00:00:00Z`).toISOString().slice(0, 10) === value;
}

type ProvenanceDateField = 'valid_date' | 'observed_day' | 'served_day';

function collectProvenanceDays(
  value: unknown,
  dates: Record<ProvenanceDateField, Set<string>> = {
    valid_date: new Set(), observed_day: new Set(), served_day: new Set(),
  },
  depth = 0,
): Record<ProvenanceDateField, Set<string>> {
  const count = Object.values(dates).reduce((total, entries) => total + entries.size, 0);
  if (depth > 12 || count >= 128) return dates;
  if (Array.isArray(value)) {
    for (const entry of value) collectProvenanceDays(entry, dates, depth + 1);
  } else if (object(value)) {
    for (const [key, entry] of Object.entries(value as Record<string, unknown>)) {
      if ((key === 'servedDay' || key === 'release_day') && isCalendarDay(entry)) {
        dates.served_day.add(entry);
      } else if (['valid_date', 'observed_day', 'served_day'].includes(key) && isCalendarDay(entry)) {
        dates[key as ProvenanceDateField].add(entry);
      } else collectProvenanceDays(entry, dates, depth + 1);
      if (Object.values(dates).reduce((total, entries) => total + entries.size, 0) >= 128) break;
    }
  }
  return dates;
}

export function regionalEvidenceAuditCall(
  id: string, stage: string, tool: string, args: Record<string, unknown>, result: unknown,
): AuditCall {
  // Literature is coordinate-free and not time-bound (CONTRACT C4): no location, dates or window.
  if (isLiteratureEvidence(tool, result)) {
    return { id, stage, tool, source: STRATEGY_KNOWLEDGE_EVIDENCE_SOURCE, ...evidenceResultStatus(result, tool) };
  }
  const selectedDate = tool === 'read_crop_cover_in_area' ? args.asOfDay : args.day ?? args.as_of_day;
  const validDate = isCalendarDay(selectedDate);
  const provenance = collectProvenanceDays(result);
  const summaries = object(result)?.layer_summaries;
  const observedFireSummaries = tool === 'fire_history_near_point' && Array.isArray(summaries)
    ? summaries.filter((summary) => Number(object(summary)?.row_count ?? 0) > 0)
    : [];
  for (const summary of observedFireSummaries) {
    for (const field of ['earliest_observed_day', 'latest_observed_day']) {
      const day = object(summary)?.[field];
      if (isCalendarDay(day)) provenance.observed_day.add(day);
    }
  }
  const validDates = [...provenance.valid_date].sort();
  const observedDates = [...provenance.observed_day].sort();
  const servedDates = [...provenance.served_day].sort();
  const source = typeof args.surface_name === 'string' ? args.surface_name.trim()
    : tool === 'drought_history_at_point' ? 'drought-areas' : tool === 'read_crop_cover_in_area' ? 'crop-cover' : '';
  const sources = observedFireSummaries.length > 0
    ? ['burn-severity', 'fire-detections'].filter((lane) => observedFireSummaries.some((summary) =>
      object(summary)?.layer_name === lane && Number(object(summary)?.row_count ?? 0) > 0))
    : [];
  const selectionRead = tool === 'surface_evidence_for_selection';
  const resolution = selectionRead ? selectedResolution(result) : {};
  const status = evidenceResultStatus(result, tool);
  const detail = selectionRead ? selectionReadDetail(validDate ? selectedDate : undefined, result) : null;
  return {
    id, stage, tool,
    ...(source.length > 0 && source.length <= 100 ? { source } : {}),
    ...(sources.length > 0 ? { sources } : {}),
    ...(validDate ? { selectedDate } : {}),
    ...(isCalendarDay(args.range_start) && isCalendarDay(args.range_end)
      ? { rangeStart: args.range_start, rangeEnd: args.range_end } : {}),
    ...(['day', 'week', 'month', 'year', 'all'].includes(String(args.time_scale)) ? { timeScale: String(args.time_scale) } : {}),
    ...(typeof args.zoom === 'number' && Number.isFinite(args.zoom) && args.zoom >= 0 && args.zoom <= 22 ? { zoom: args.zoom } : {}),
    ...(validDates.length > 0 ? { validDates } : {}),
    ...(observedDates.length > 0 ? { observedDates } : {}),
    ...(servedDates.length > 0 ? { servedDates } : {}),
    ...(typeof args.latitude === 'number' && Number.isFinite(args.latitude) && Math.abs(args.latitude) <= 90
      && typeof args.longitude === 'number' && Number.isFinite(args.longitude) && Math.abs(args.longitude) <= 180
      ? { location: { lat: args.latitude, lon: args.longitude } } : {}),
    ...resolution,
    ...(selectionRead && (isRegionalStaticSurface(source) || isStaticResult(result)) ? { staticLayer: true } : {}),
    ...status,
    // Per-read availability detail lives on the tool-call record; limitations carry one line per lane.
    ...(detail && status.status === 'observed' ? { summary: `${status.summary ?? ''} ${detail}`.trim().slice(0, 2_000) } : {}),
    ...(detail && status.status !== 'observed' ? { reason: `${status.reason ?? ''} ${detail}`.trim().slice(0, 2_000) } : {}),
  };
}

function selectionLanes(result: unknown): Record<string, unknown>[] {
  const lanes = object(result)?.lanes;
  return Array.isArray(lanes) ? lanes.map(object).filter((lane) => lane !== null) : [];
}

function selectedEntries(result: unknown): Record<string, unknown>[] {
  return selectionLanes(result).map((lane) => object(lane.selected)).filter((entry) => entry !== null);
}

function hasRecords(entry: Record<string, unknown>): boolean {
  return ['features', 'rows'].some((field) => Array.isArray(entry[field]) && (entry[field] as unknown[]).length > 0);
}

function entryState(entry: Record<string, unknown>): string {
  return typeof entry.state === 'string' ? entry.state.slice(0, 80) : 'state not reported';
}

/** A static (`static_lookup`) result: an explicit marker, or every returned lane declares that nature. */
function isStaticResult(result: unknown): boolean {
  const marked = (node: Record<string, unknown> | null) => node?.static === true || node?.static_layer === true;
  const lanes = selectionLanes(result);
  return marked(object(result)) || lanes.some((lane) => marked(lane) || marked(object(lane.selected)))
    || (lanes.length > 0 && lanes.every((lane) => lane.lane_nature === 'static_lookup'));
}

function calendarDayDifference(from: string, to: string): number {
  return Math.round((Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`)) / 86_400_000);
}

/** A nearest-support distance the audit can carry (agri `distance_km`; schema max 5,000 km). */
function plausibleDistanceKm(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value > 0 && value <= 5_000;
}

function entryFeatures(entry: Record<string, unknown>): Record<string, unknown>[] {
  return Array.isArray(entry.features) ? entry.features.map(object).filter((feature) => feature !== null) : [];
}

/** Whether a selected entry's support contains the point: its own summary, or any containing feature. */
function entryCovers(entry: Record<string, unknown>): boolean {
  return entry.spatial_relation === 'covers'
    || entryFeatures(entry).some((feature) => feature.spatial_relation === 'covers' || feature.covers_probe_point === true);
}

/**
 * The agri reader's nearest-day and nearest-cell resolution of the selected read: `published_nearest`
 * (served_day, signed day_offset), and the SELECTED entry's `spatial_relation`/`distance_km` (agri
 * agent/AGENTS.md contract table) -- soil survey's features carry neither. Per-feature `nearest_cell`
 * is the fallback for readers that predate the entry summary. `nearest_area_outside` (a polygon the
 * point is NOT inside) never becomes `cellDistanceKm`: it is not the value here. Absent fields leave
 * the audit exactly as before.
 */
function selectedResolution(result: unknown): Pick<AuditCall, 'resolvedDay' | 'dayOffset' | 'cellDistanceKm'> {
  const selected = selectedEntries(result);
  const resolution: Pick<AuditCall, 'resolvedDay' | 'dayOffset' | 'cellDistanceKm'> = {};
  const nearestDay = selected.find((entry) => entry.state === 'published_nearest' && isCalendarDay(entry.served_day) && hasRecords(entry));
  if (nearestDay && isCalendarDay(nearestDay.served_day)) {
    // The two named days are ground truth; `day_offset` is used only when the requested day is absent.
    const offset = isCalendarDay(nearestDay.requested_day) ? calendarDayDifference(nearestDay.requested_day, nearestDay.served_day)
      : typeof nearestDay.day_offset === 'number' && Number.isInteger(nearestDay.day_offset) ? nearestDay.day_offset : null;
    if (offset !== null && offset !== 0 && Math.abs(offset) <= 3_660) {
      resolution.resolvedDay = nearestDay.served_day;
      resolution.dayOffset = offset;
    }
  }
  if (!selected.some(entryCovers)) {
    const distances = selected.flatMap((entry) => entry.spatial_relation === 'nearest_cell' ? [entry.distance_km]
      : entry.spatial_relation === undefined
        ? entryFeatures(entry).filter((feature) => feature.spatial_relation === 'nearest_cell').map((feature) => feature.distance_km)
        : []).filter(plausibleDistanceKm);
    if (distances.length > 0) resolution.cellDistanceKm = Math.round(Math.min(...distances) * 10) / 10;
  }
  return resolution;
}

/** The history envelope's checked days, completeness and continuation, or null when absent. */
function selectionHistory(result: unknown) {
  const history = object(object(result)?.history);
  if (!history) return null;
  const days = Array.isArray(history.sampled_days) ? [...new Set(history.sampled_days.filter(isCalendarDay))].sort() : null;
  const entries = selectionLanes(result).flatMap((lane) => Array.isArray(lane.history) ? lane.history.map(object) : [])
    .filter((entry) => entry !== null);
  const empty = entries.filter((entry) => !hasRecords(entry));
  const nextPageStart = typeof history.next_page_start === 'number' && Number.isSafeInteger(history.next_page_start)
    && history.next_page_start >= 0 ? history.next_page_start : null;
  return {
    days, empty, nextPageStart,
    complete: history.complete === true ? true : history.complete === false ? false : null,
    state: typeof history.state === 'string' ? history.state.slice(0, 80) : null,
  };
}

/** Full per-read availability detail for the tool-call record (not the limitations list). */
function selectionReadDetail(selectedDate: string | undefined, result: unknown): string | null {
  const selected = selectedEntries(result);
  const history = selectionHistory(result);
  const details: string[] = [];
  if (selected.length) {
    const empty = selected.filter((entry) => !hasRecords(entry));
    details.push(`Selected ${selectedDate ?? 'day'}: ${selected.length - empty.length}/${selected.length} lane responses with records${empty.length ? ` (without records: ${[...new Set(empty.map(entryState))].join(', ')})` : ''}.`);
  }
  if (history) {
    details.push(history.days
      ? `History checked ${history.days.length} calendar date(s)${history.days.length ? `: ${history.days.slice(0, 31).join(', ')}${history.days.length > 31 ? ` (+${history.days.length - 31} more)` : ''}` : ''}.`
      : 'History declares no checked calendar-day list.');
    details.push(`History completeness: ${history.complete === true ? 'complete' : history.complete === false ? 'incomplete' : 'unknown'}.`);
    const states = new Map<string, Set<string>>();
    for (const entry of history.empty) {
      const state = entryState(entry);
      if (!history.days || !isCalendarDay(entry.requested_day) || !history.days.includes(entry.requested_day)
        || (!states.has(state) && states.size >= 4)) continue;
      states.set(state, (states.get(state) ?? new Set<string>()).add(entry.requested_day));
    }
    for (const [state, dates] of states) {
      const listed = [...dates].sort();
      details.push(`${state} at ${listed.slice(0, 5).join(', ')}${listed.length > 5 ? ` (+${listed.length - 5} more)` : ''}.`);
    }
    if (history.nextPageStart !== null) details.push(`Continuation page_start ${history.nextPageStart}.`);
    if (history.state) details.push(`History state: ${history.state}.`);
  }
  return details.length ? details.join(' ') : null;
}

/** The nearest published day agri names beside an unpublished selected entry (`nearest_published_day`). */
export interface RegionalNearestPublishedDay {
  requestedDay: string | null;
  /** The selected entry's own state: `day_not_written` or `governed_absence`. */
  state: string;
  /** Null when agri searched and found no other published day. */
  day: string | null;
  offset: number | null;
  toleranceDays: number | null;
}

/** What one read contributes to its lane's single limitation line. */
export interface RegionalLaneReadNote {
  audit: AuditCall;
  selectedRecords: boolean;
  emptySelectedStates: string[];
  history: ReturnType<typeof selectionHistory>;
  noRenderableFacts: boolean;
  nearestPublished?: RegionalNearestPublishedDay | null;
  /** Polygon lanes: the point is inside no area; the nearest area's centroid distance, information only. */
  outsideAreaKm?: number | null;
  /** What the used nearest-support distance measured: `cell_edge`, `source_coordinate`, `delineation_edge`. */
  nearestBasis?: string | null;
}

function nearestPublishedOf(result: unknown): RegionalNearestPublishedDay | null {
  for (const lane of selectionLanes(result)) {
    const entry = object(lane.selected);
    if (!entry || hasRecords(entry) || !('nearest_published_day' in entry)) continue;
    const offset = entry.nearest_day_offset;
    const tolerance = lane.tolerance_days;
    return {
      requestedDay: isCalendarDay(entry.requested_day) ? entry.requested_day : null,
      state: entryState(entry),
      day: isCalendarDay(entry.nearest_published_day) ? entry.nearest_published_day : null,
      offset: typeof offset === 'number' && Number.isInteger(offset) && offset !== 0 ? offset : null,
      toleranceDays: typeof tolerance === 'number' && Number.isInteger(tolerance) && tolerance >= 0 ? tolerance : null,
    };
  }
  return null;
}

export function regionalLaneReadNote(audit: AuditCall, result: unknown, noRenderableFacts = false): RegionalLaneReadNote {
  const selected = selectedEntries(result);
  const covered = selected.some(entryCovers);
  const outside = covered ? [] : selected.filter((entry) => entry.spatial_relation === 'nearest_area_outside')
    .map((entry) => entry.distance_km).filter(plausibleDistanceKm);
  const basis = selected.find((entry) => entry.spatial_relation === 'nearest_cell')?.distance_km_basis;
  return {
    audit,
    selectedRecords: selected.some(hasRecords),
    emptySelectedStates: [...new Set(selected.filter((entry) => !hasRecords(entry)).map(entryState))],
    // A local read's one-day "history" is its selected day again, never a history pass.
    history: audit.staticLayer || audit.stage === 'local' ? null : selectionHistory(result),
    noRenderableFacts,
    nearestPublished: nearestPublishedOf(result),
    outsideAreaKm: outside.length ? Math.round(Math.min(...outside) * 10) / 10 : null,
    nearestBasis: typeof basis === 'string' ? basis.slice(0, 40) : null,
  };
}

/**
 * SPARSE-area surfaces (outside every polygon is the answer) and what their gap line calls an area.
 * Mirrors agri `selection_reads.SPARSE_AREA_LANES`; tiling polygon lanes (crop-cover, watersheds)
 * are deliberately absent: their nearest polygon is a used `nearest_cell`. Pinned by an agri test.
 */
export const REGIONAL_SPARSE_AREA_NOUNS: Readonly<Record<string, string>> = {
  'drought-areas': 'drought area', 'fire-perimeters': 'fire perimeter', 'evacuation-zones': 'evacuation zone',
  'burn-severity': 'burn perimeter', 'land-context-boundaries': 'land boundary',
};

/** The used nearest support, named by what its distance measured (agri `distance_km_basis`). */
function nearestSupportText(basis: string | null | undefined, km: number): string {
  if (basis === 'source_coordinate') return `no station at the point; nearest station ${km} km (used)`;
  if (basis === 'delineation_edge') return `no soil map unit covers the point; nearest delineation ${km} km (used)`;
  if (basis === 'geometry_centroid') return `no cell covers the point; nearest cell ${km} km to its centroid (used)`;
  return `no cell covers the point; nearest cell ${km} km (used)`;
}

/** "no record on 2026-10-04; nearest published 2026-08-25 (40 d earlier, beyond the 3-day tolerance)". */
function nearestPublishedText(nearest: RegionalNearestPublishedDay, fallbackDay: string | undefined): string {
  const requested = nearest.requestedDay ?? fallbackDay ?? 'the selected day';
  const governed = nearest.state === 'governed_absence';
  const head = governed ? `governed absence on ${requested} (published as no records, not a gap)` : `no record on ${requested}`;
  if (!nearest.day || nearest.offset === null) return `${head}; no other published day found`;
  const distance = `${Math.abs(nearest.offset)} d ${nearest.offset < 0 ? 'earlier' : 'later'}`;
  const beyond = nearest.toleranceDays !== null && Math.abs(nearest.offset) > nearest.toleranceDays;
  const qualifier = beyond ? `, beyond the ${nearest.toleranceDays}-day tolerance` : governed ? ', context only' : '';
  return `${head}; nearest published ${nearest.day} (${distance}${qualifier})`;
}

const STAGE_READ_LABELS: Record<string, string> = { local: 'selected-day', temporal: 'history', additional: 'additional' };

/**
 * ONE concise line per lane, folding its selected-day and history reads together, e.g.
 * "vegetation: no cell covers the point; nearest cell 23.6 km (used)". Null when nothing is owed.
 * The `<source>: ` prefix is what `regional-evidence-presentation.ts` files under the lane's row.
 */
export function regionalLaneLimitation(source: string, notes: readonly RegionalLaneReadNote[]): string | null {
  const parts: string[] = [];
  if (notes.some(({ audit }) => audit.staticLayer)) parts.push('static layer, current release');
  const nearestCell = notes.find(({ audit }) => (audit.cellDistanceKm ?? 0) > 0);
  if (nearestCell) parts.push(nearestSupportText(nearestCell.nearestBasis, nearestCell.audit.cellDistanceKm ?? 0));
  const outside = notes.find((note) => (note.outsideAreaKm ?? 0) > 0);
  if (outside) parts.push(`not inside any ${REGIONAL_SPARSE_AREA_NOUNS[source] ?? 'mapped area'}; nearest ${outside.outsideAreaKm} km (to its centroid)`);
  const nearestDay = notes.find(({ audit }) => audit.resolvedDay && audit.dayOffset)?.audit;
  if (nearestDay) {
    parts.push(`${nearestDay.selectedDate ?? 'selected day'} not published; nearest published day ${nearestDay.resolvedDay} (${(nearestDay.dayOffset ?? 0) > 0 ? '+' : ''}${nearestDay.dayOffset} d, used)`);
  }
  const informed = notes.find((note) => note.nearestPublished);
  if (informed?.nearestPublished) parts.push(nearestPublishedText(informed.nearestPublished, informed.audit.selectedDate));
  const emptySelected = notes.find((note) => note.emptySelectedStates.length > 0);
  if (emptySelected && !informed && !notes.some((note) => note.selectedRecords)) {
    parts.push(`no records on ${emptySelected.audit.selectedDate ?? 'the selected day'} (${emptySelected.emptySelectedStates.join(', ')})`);
  }
  for (const { history } of notes) {
    if (!history || (history.complete === true && history.empty.length === 0)) continue;
    parts.push(`history ${history.days ? `${history.days.length} sampled day(s)` : 'sampled days not declared'}`
      + `${history.empty.length ? `, ${history.empty.length} lane-day(s) without records` : ''}`
      + `${history.complete === false ? ', incomplete' : history.complete === null ? ', completeness unknown' : ''}`
      + `${history.nextPageStart !== null ? ` (next page_start ${history.nextPageStart})` : ''}`);
  }
  for (const { audit, emptySelectedStates, history } of notes) {
    const stage = STAGE_READ_LABELS[audit.stage] ?? audit.stage;
    if (['refused', 'error', 'not_queried'].includes(audit.status)) {
      parts.push(`${stage} read ${audit.status}${audit.reason ? ` (${audit.reason.slice(0, 80)})` : ''}`);
    } else if (audit.status === 'unavailable' && emptySelectedStates.length === 0 && !history) {
      parts.push(`${stage} read returned no records`);
    }
  }
  if (notes.some((note) => note.noRenderableFacts)) parts.push('records returned but no renderable measurement facts');
  const unique = [...new Set(parts)];
  return unique.length ? `${source}: ${unique.join('; ')}`.slice(0, 600) : null;
}

/** One read's limitation line (the additional-read path); the prefetch folds reads per lane instead. */
export function regionalEvidenceLimitations(audit: AuditCall, result: unknown): string[] {
  if (audit.tool !== 'surface_evidence_for_selection' || !audit.source || !object(result)) return [];
  const line = regionalLaneLimitation(audit.source, [regionalLaneReadNote(audit, result)]);
  return line ? [line] : [];
}

export interface RegionalAnalysisWorkflow {
  catalogue: RegionalEvidenceCatalogue | null;
  evidence: RegionalAnalysisEvidence;
  measurementFacts: RegionalMeasurementFacts;
  /** Read ledger for the literature `server_context` site facts; additional reads append to it. */
  siteFactObservations: SiteFactObservation[];
  context: string;
}

/** Tools whose results describe coverage or catalogue metadata, never a value at the point. */
const METADATA_ONLY_TOOLS = ['observation_coverage_on_day', 'observation_temporal_neighbors', 'list_environmental_layers'];

/** Admit facts only from executed measured reads for a declared concrete source. */
export function regionalFactsForRead(audit: AuditCall, result: unknown): RegionalMeasurementFacts {
  if (audit.status !== 'observed' || !audit.source || !(REGIONAL_TOOL_EVIDENCE_SOURCES as readonly string[]).includes(audit.source)
    || audit.source === STRATEGY_KNOWLEDGE_EVIDENCE_SOURCE || isStrategyKnowledgeTool(audit.tool)
    || METADATA_ONLY_TOOLS.includes(audit.tool)) return { facts: [], omittedFacts: 0 };
  return buildRegionalMeasurementFacts([{ id: audit.id, source: audit.source, result }]);
}

/** One measured site value read this request, before the agreement check in `literatureSiteFacts`. */
export interface SiteFactObservation {
  fact: keyof LiteratureSiteFacts | 'fire_day' | 'burn_severity_by_day';
  /** For `burn_severity_by_day`, `${fireDay}|${severity}` -- pairs the class to the fire it was read from. */
  value: number | string;
  readId: string;
}

type NumericSiteFact = 'soil_ph' | 'soil_organic_carbon_pct' | 'sand_pct' | 'clay_pct' | 'electrical_conductivity_ds_m' | 'annual_precip_mm';

/** Plausible bounds after unit conversion; a value outside them is dropped as a unit mismatch. */
const SITE_FACT_BOUNDS: Record<NumericSiteFact, [number, number]> = {
  soil_ph: [2, 12], soil_organic_carbon_pct: [0, 60], sand_pct: [0, 100], clay_pct: [0, 100],
  electrical_conductivity_ds_m: [0, 200], annual_precip_mm: [0, 15_000],
};
const PERCENT_UNITS = new Set(['%', 'percent', 'pct']);
/** MTBS thematic burn-severity classes 2-4 plus their names; classes 1, 5 and 6 carry no severity. */
const BURN_SEVERITY_CLASSES: Record<string, NonNullable<LiteratureSiteFacts['burn_severity']>> = {
  '2': 'low', '3': 'moderate', '4': 'high', low: 'low', moderate: 'moderate', high: 'high',
};
/** NLCD 2019 Anderson Level II class codes. */
const NLCD_CLASS_CODES = new Set([11, 12, 21, 22, 23, 24, 31, 41, 42, 43, 51, 52, 71, 72, 73, 74, 81, 82, 90, 95]);
const SITE_FACT_OBSERVATIONS_PER_READ = 32;
const MAX_DAYS_SINCE_FIRE = 36_500;

/** Which S1 site fact a metric or surface name reports, by name token; see services/AGENTS.md §literature-server-context. */
function siteFactForName(name: string): NumericSiteFact | null {
  const tokens = name.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
  if (tokens.includes('ph') || tokens.includes('phh2o')) return 'soil_ph';
  if (tokens.includes('soc') || (tokens.includes('organic') && tokens.includes('carbon'))) return 'soil_organic_carbon_pct';
  if (tokens.includes('sand')) return 'sand_pct';
  if (tokens.includes('clay')) return 'clay_pct';
  if (tokens.includes('ec') || tokens.includes('conductivity')) return 'electrical_conductivity_ds_m';
  if (tokens.includes('annual') && tokens.some((token) => token.startsWith('precip'))) return 'annual_precip_mm';
  return null;
}

/** Convert one value to its S1 unit, or null when the unit is missing, not understood or implausible. */
function siteFactValue(fact: NumericSiteFact, value: number, rawUnit: string): number | null {
  const unit = rawUnit.toLowerCase().replace(/\s+/g, '').replace('µ', 'u').replace('×', 'x');
  const converted = (() => {
    switch (fact) {
      // SoilGrids publishes pH x10 (`phh2o` 62 = pH 6.2).
      case 'soil_ph': return unit === 'ph' ? value : ['ph*10', 'phx10'].includes(unit) ? value / 10 : null;
      // SoilGrids SOC is dg/kg; g/kg / 10 = mass percent.
      case 'soil_organic_carbon_pct': return unit === 'g/kg' ? value / 10 : unit === 'dg/kg' ? value / 100
        : PERCENT_UNITS.has(unit) ? value : null;
      // SoilGrids sand and clay are g/kg.
      case 'sand_pct': case 'clay_pct': return PERCENT_UNITS.has(unit) || unit === 'g/100g' ? value
        : unit === 'g/kg' ? value / 10 : null;
      case 'electrical_conductivity_ds_m': return ['ds/m', 'ms/cm'].includes(unit) ? value : unit === 'us/cm' ? value / 1000 : null;
      // Daily (mm/day) precipitation is never summed into an annual total here.
      case 'annual_precip_mm': return ['mm/yr', 'mm/year', 'mm/a'].includes(unit) ? value : null;
    }
  })();
  if (converted === null || !Number.isFinite(converted)) return null;
  const [minimum, maximum] = SITE_FACT_BOUNDS[fact];
  return converted >= minimum && converted <= maximum ? Math.round(converted * 100) / 100 : null;
}

function calendarDayOf(value: unknown): string | null {
  if (isCalendarDay(value)) return value;
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}T/.test(value) || !Number.isFinite(Date.parse(value))) return null;
  const day = value.slice(0, 10);
  return isCalendarDay(day) ? day : null;
}

/** Records whose own support contains the selected point, tagged selected-day or sampled history. */
function pointRecords(result: unknown): Array<{ properties: Record<string, unknown>; selected: boolean }> {
  const records: Array<{ properties: Record<string, unknown>; selected: boolean }> = [];
  const visit = (value: unknown, selected: boolean, depth: number) => {
    const node = object(value);
    if (!node || depth > 8 || node.error || node.refusal_code) return;
    for (const [key, entry] of Object.entries(node)) {
      if ((key === 'features' || key === 'rows') && Array.isArray(entry)) {
        for (const item of entry) {
          const row = object(item);
          if (row?.covers_probe_point === true && row.allowed_client_exposure !== false) {
            records.push({ properties: object(row.properties) ?? row, selected });
          }
        }
      } else if (key === 'selected') visit(entry, true, depth + 1);
      else if (key === 'history' && Array.isArray(entry)) entry.forEach((child) => visit(child, false, depth + 1));
      else if (key === 'lanes' && Array.isArray(entry)) entry.forEach((child) => visit(child, selected, depth + 1));
    }
  };
  visit(result, true, 0);
  return records;
}

/** Extract S1 site values from one executed measured read; only records containing the point count. */
export function siteFactObservationsForRead(audit: AuditCall, result: unknown): SiteFactObservation[] {
  if (audit.status !== 'observed' || !audit.source || audit.source === STRATEGY_KNOWLEDGE_EVIDENCE_SOURCE
    || isStrategyKnowledgeTool(audit.tool) || METADATA_ONLY_TOOLS.includes(audit.tool)) return [];
  const source = audit.source;
  const observations: SiteFactObservation[] = [];
  const add = (fact: SiteFactObservation['fact'], value: number | string) => {
    if (observations.length < SITE_FACT_OBSERVATIONS_PER_READ) observations.push({ fact, value, readId: audit.id });
  };
  for (const { properties, selected } of pointRecords(result)) {
    // A fire's own date: MTBS `ignition_date` (its `observed_day` is the publication day) or the
    // perimeter's discovery instant. Satellite detections are thermal anomalies, not fire dates.
    const fireDay = source === 'burn-severity' ? calendarDayOf(properties.ignition_date)
      : source === 'fire-perimeters' ? calendarDayOf(properties.fire_discovery_at) : null;
    if (fireDay) add('fire_day', fireDay);
    // Paired to fireDay, not a bare 'burn_severity' fact: a record's severity is only ever trusted
    // for ITS OWN fire (wave-2 fix-stage review, `burn_severity_by_day` decoded in `literatureSiteFacts`),
    // never applied to whichever fire in this read turns out to be newest.
    if (source === 'burn-severity' && fireDay && (typeof properties.severity_class === 'string' || typeof properties.severity_class === 'number')) {
      const severity = BURN_SEVERITY_CLASSES[String(properties.severity_class).trim().toLowerCase()];
      if (severity) add('burn_severity_by_day', `${fireDay}|${severity}`);
    }
    if (!selected) continue;
    for (const [valueKey, unitKey, nameKey] of [
      ['normalized_value', 'normalized_unit', 'signal_name'], ['metric_value', 'metric_unit', 'metric_name'],
    ] as const) {
      const value = properties[valueKey];
      const unit = properties[unitKey];
      if (typeof value !== 'number' || typeof unit !== 'string') continue;
      const name = properties[nameKey];
      const fact = siteFactForName(typeof name === 'string' ? name : source);
      const converted = fact ? siteFactValue(fact, value, unit) : null;
      if (fact && converted !== null) add(fact, converted);
    }
    const coverName = [properties.nlcd_class_name, properties.land_cover_class, properties.land_cover]
      .find((entry): entry is string => typeof entry === 'string' && entry.trim().length > 0 && entry.length <= 80);
    const coverCode = [properties.nlcd_class, properties.nlcd_code]
      .find((entry): entry is number => typeof entry === 'number' && NLCD_CLASS_CODES.has(entry));
    if (coverName) add('land_cover', coverName.trim());
    else if (coverCode !== undefined) add('land_cover', String(coverCode));
  }
  return observations;
}

/** SoilGrids values already in the assembled payload: `ph` is unscaled pH, `organicCarbon` is g/kg. */
function payloadSoilObservations(payload: RegionalContextPayload): SiteFactObservation[] {
  const soil = payload.soilProperties;
  if (!soil) return [];
  const readId = 'initial-soilProperties';
  const ph = siteFactValue('soil_ph', soil.ph, 'pH');
  const organicCarbon = siteFactValue('soil_organic_carbon_pct', soil.organicCarbon, 'g/kg');
  return [
    ...(ph !== null ? [{ fact: 'soil_ph' as const, value: ph, readId }] : []),
    ...(organicCarbon !== null ? [{ fact: 'soil_organic_carbon_pct' as const, value: organicCarbon, readId }] : []),
  ];
}

/** Fold this request's observations into S1 site facts; a fact whose reads disagree is omitted. */
export function literatureSiteFacts(observations: readonly SiteFactObservation[], serverCurrentDate: string): LiteratureSiteFacts {
  const values = new Map<Exclude<SiteFactObservation['fact'], 'burn_severity_by_day'>, Set<number | string>>();
  const severityByDay = new Map<string, Set<NonNullable<LiteratureSiteFacts['burn_severity']>>>();
  for (const observation of observations) {
    if (observation.fact === 'burn_severity_by_day') {
      const [fireDay, severity] = String(observation.value).split('|');
      const entries = severityByDay.get(fireDay) ?? new Set<NonNullable<LiteratureSiteFacts['burn_severity']>>();
      entries.add(severity as NonNullable<LiteratureSiteFacts['burn_severity']>);
      severityByDay.set(fireDay, entries);
      continue;
    }
    const entries = values.get(observation.fact) ?? new Set<number | string>();
    entries.add(observation.value);
    values.set(observation.fact, entries);
  }
  const facts: LiteratureSiteFacts = {};
  for (const [fact, entries] of values) {
    if (fact === 'fire_day' || entries.size !== 1) continue;
    // Each observation was typed and unit-converted for its own key by `siteFactObservationsForRead`.
    Object.assign(facts, { [fact]: [...entries][0] });
  }
  // The most recent fire containing the point, counted from the server's today.
  const today = Date.parse(`${serverCurrentDate}T00:00:00Z`);
  const fireDays = [...(values.get('fire_day') ?? [])]
    .filter((day): day is string => isCalendarDay(day) && day <= serverCurrentDate).sort();
  const latestFire = fireDays.at(-1);
  if (latestFire && Number.isFinite(today)) {
    const days = Math.round((today - Date.parse(`${latestFire}T00:00:00Z`)) / 86_400_000);
    if (days >= 0 && days <= MAX_DAYS_SINCE_FIRE) facts.days_since_fire = days;
    // Same-fire pairing (wave-2 fix-stage review): burn_severity is trusted only for the fire that
    // sets days_since_fire -- an older or unrelated burn-severity record's class never leaks in.
    const severityForLatestFire = severityByDay.get(latestFire);
    if (severityForLatestFire?.size === 1) facts.burn_severity = [...severityForLatestFire][0];
  }
  return facts;
}

const MAX_LITERATURE_QUESTION_CHARACTERS = 2_000;
/** The marker `conversation-history.ts` prefixes to trimmed replay; not the user's words. */
const REPLAY_OMISSION_MARKER = /\[Additional saved content omitted[^\]]*\]/g;
/** The route's no-question filler, saved as a user turn; not the user's words. Exported so `route.ts`'s
 * `DEFAULT_QUESTION` imports this one literal instead of repeating it (wave-2 fix-stage review). */
export const SAVED_DEFAULT_QUESTION = 'Analyze this location';

/** The user's own messages this conversation (S1 `user_question`): verbatim, latest last, front-truncated. */
export function literatureUserQuestion(history: readonly ConversationTurn[], userQuestion?: string): string | undefined {
  const messages = [...history.filter((turn) => turn.role === 'user').map((turn) => turn.content), ...(userQuestion ? [userQuestion] : [])]
    .map((message) => message.replace(REPLAY_OMISSION_MARKER, ' ').replace(/[\u0000-\u001F\u007F-\u009F]+/g, ' ').trim())
    .filter((message) => message.length > 0 && message !== SAVED_DEFAULT_QUESTION);
  let joined = messages.join('\n');
  if (joined.length > MAX_LITERATURE_QUESTION_CHARACTERS) {
    joined = joined.slice(-MAX_LITERATURE_QUESTION_CHARACTERS);
    // Never start on the second half of a surrogate pair.
    if (/^[\uDC00-\uDFFF]/.test(joined)) joined = joined.slice(1);
  }
  joined = joined.trim();
  return joined.length > 0 ? joined : undefined;
}

/** Build the out-of-band literature context (seam S1) from this request's point, words and measured reads. */
export function buildLiteratureServerContext(
  payload: RegionalContextPayload, temporal: TemporalContext, history: readonly ConversationTurn[],
  userQuestion: string | undefined, observations: readonly SiteFactObservation[],
): LiteratureServerContext {
  const question = literatureUserQuestion(history, userQuestion);
  const { lon: longitude, lat: latitude } = payload.location;
  const point = Number.isFinite(longitude) && Number.isFinite(latitude) && Math.abs(longitude) <= 180 && Math.abs(latitude) <= 90
    ? { longitude, latitude } : undefined;
  const siteFacts = literatureSiteFacts([...payloadSoilObservations(payload), ...observations], temporal.serverCurrentDate);
  return {
    ...(question ? { user_question: question } : {}),
    ...(point ? { point } : {}),
    ...(Object.keys(siteFacts).length > 0 ? { site_facts: siteFacts } : {}),
  };
}

/** Derive a stage result from all of its completed and attempted reads. */
export function regionalEvidenceStageStatus(entries: AuditCall[]): RegionalAnalysisEvidence['stages'][number]['status'] {
  const admissible = (entry: AuditCall) => ['observed', 'answered', 'answered_no_records', 'governed_absence'].includes(entry.status);
  if (entries.length > 0 && entries.every(admissible)) return 'completed';
  if (entries.some(admissible)) return 'partial';
  return 'unavailable';
}

/** A whole-call or selected-day refusal that names process state (a busy slot), not the arguments. */
function isTransientRefusal(result: unknown): boolean {
  const transient = (node: Record<string, unknown> | null) => typeof node?.refusal_code === 'string' && TRANSIENT_REFUSAL_CODES.has(node.refusal_code);
  return transient(object(result)) || selectedEntries(result).some(transient);
}

function isTransientHttpFailure(error: unknown): boolean {
  return error instanceof UpstreamHttpError && TRANSIENT_HTTP_STATUSES.has(error.status)
    && !NON_TRANSIENT_BRIDGE_CODES.some((code) => error.bodyText?.includes(code));
}

/** 200-600 ms jittered pause that ends early (without throwing) when the stage aborts. */
function retryPause(signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const done = () => { clearTimeout(timer); signal.removeEventListener('abort', done); resolve(); };
    const timer = setTimeout(done, 200 + Math.floor(Math.random() * 400));
    signal.addEventListener('abort', done, { once: true });
  });
}

/** Read once; a transient capacity refusal or 429/502/503 is retried exactly once after a jittered pause. */
async function readEvidenceWithRetry(tool: string, args: Record<string, unknown>, signal: AbortSignal): Promise<unknown> {
  const attempt = async (): Promise<{ result: unknown } | { failure: unknown }> => {
    try {
      return { result: JSON.parse(await callRegionalEvidenceTool(tool, args, signal)) as unknown };
    } catch (error) {
      if (isTransientHttpFailure(error)) return { failure: error };
      throw error;
    }
  };
  let outcome = await attempt();
  if ('failure' in outcome || isTransientRefusal(outcome.result)) {
    await retryPause(signal);
    if (!signal.aborted) outcome = await attempt();
  }
  if ('failure' in outcome) throw outcome.failure;
  return outcome.result;
}

/** Run the evidence graph before synthesis; see services/AGENTS.md for budgets and transfer limits. */
export async function prepareRegionalAnalysis(
  payload: RegionalContextPayload,
  temporal: TemporalContext,
  signal?: AbortSignal,
): Promise<RegionalAnalysisWorkflow> {
  const evidence: RegionalAnalysisEvidence = {
    version: 1,
    stages: REGIONAL_ANALYSIS_STAGES.map(([id, label]) => ({ id, label, status: 'unavailable' })),
    toolCalls: [],
    limitations: [
      'History pages sample the trailing requested window, which never extends past today. Sampling, omitted days and continuation must be reported; samples do not establish a continuous trend or seasonal baseline. Unchecked dates remain unknown, and no returned records is not evidence of environmental absence.',
      'Missing land-use, livestock, terrain, fuel and amendment-quality measurements remain feasibility checks, not assumed site facts.',
    ],
  };
  let catalogue: RegionalEvidenceCatalogue | null = null;
  const results: EvidenceRead[] = [];
  const measurementFacts: RegionalMeasurementFacts = { facts: [], omittedFacts: 0 };
  const siteFactObservations: SiteFactObservation[] = [];
  try { catalogue = await loadRegionalEvidenceTools(signal); }
  catch (error) { if (signal?.aborted) throw error; }
  if (!catalogue) {
    evidence.limitations.push('The environmental tool catalogue could not be loaded. Only the supplied regional context was available; historical and regional comparisons were not performed.');
    return { catalogue, evidence, measurementFacts, siteFactObservations, context: JSON.stringify({ evidence, measurementFacts, strategyScreening: STRATEGY_SCREENING }) };
  }
  evidence.stages[0].status = 'completed';
  const tools = new Set(catalogue.tools.map((tool) => tool.name));

  const runStage = async (stage: string, requests: ReadRequest[], timeoutMs: number) => {
    const controller = new AbortController();
    const abort = () => controller.abort(signal?.reason);
    signal?.addEventListener('abort', abort, { once: true });
    if (signal?.aborted) abort();
    const timer = setTimeout(() => controller.abort(new Error('Evidence stage deadline reached')), timeoutMs);
    let next = 0;
    const entries: AuditCall[] = [];
    try {
      await Promise.all(Array.from({ length: STAGE_CONCURRENCY }, async () => {
        while (next < requests.length) {
          const request = requests[next++];
          const id = `${stage}-${next}`;
          const base = { ...regionalEvidenceAuditCall(id, stage, request.tool, request.args, { error: 'read_not_executed' }),
            ...(request.source ? { source: request.source } : {}) };
          const note = (audit: AuditCall, result: unknown = null, noRenderableFacts = false) => {
            if (request.source) laneNotes.set(request.source, [...laneNotes.get(request.source) ?? [], regionalLaneReadNote(audit, result, noRenderableFacts)]);
          };
          if (controller.signal.aborted || !tools.has(request.tool)) {
            const skipped: AuditCall = { ...base, status: 'not_queried', reason: controller.signal.aborted ? 'The reserved stage time budget was exhausted.' : 'The deployed catalogue does not expose this reader.' };
            entries.push(skipped);
            note(skipped);
            continue;
          }
          try {
            const result = await readEvidenceWithRetry(request.tool, request.args, controller.signal);
            const audit = { ...regionalEvidenceAuditCall(id, stage, request.tool, request.args, result), ...(request.source ? { source: request.source } : {}) };
            entries.push(audit);
            if (audit.staticLayer && request.source) staticSources.add(request.source);
            const readFacts = regionalFactsForRead(audit, result);
            measurementFacts.facts.push(...readFacts.facts);
            measurementFacts.omittedFacts += readFacts.omittedFacts;
            siteFactObservations.push(...siteFactObservationsForRead(audit, result));
            note(audit, result, audit.status === 'observed' && readFacts.facts.length === 0);
            results.push({ id, evidenceReadId: id, evidenceSource: audit.source, evidenceStatus: audit.status, result: boundedEvidence(result) });
          } catch (error) {
            if (signal?.aborted) throw error;
            const failed: AuditCall = { ...base, status: 'error', reason: controller.signal.aborted ? 'The evidence read exceeded its reserved stage deadline.' : 'The environmental tool read failed.' };
            entries.push(failed);
            note(failed);
          }
        }
      }));
    } finally {
      clearTimeout(timer);
      signal?.removeEventListener('abort', abort);
    }
    evidence.toolCalls.push(...entries);
    const node = evidence.stages.find((entry) => entry.id === stage);
    if (node) node.status = regionalEvidenceStageStatus(entries);
  };

  const selectedSurfaces = [
    ...temporal.readings.map((row) => regionalSurfaceName(row.layer)),
    ...Object.keys(temporal.analysisSelection?.layerDays ?? {}).map(regionalSurfaceName),
  ];
  const surfaces = regionalInitialSurfaces(selectedSurfaces, catalogue.surfaces);
  const laneNotes = new Map<string, RegionalLaneReadNote[]>();
  const staticSources = new Set(surfaces.filter(isRegionalStaticSurface));
  await runStage('local', surfaces.map((source) => ({
    source,
    tool: 'surface_evidence_for_selection',
    args: regionalSelectionArguments(payload, temporal, source, false),
  })), 12_000);
  // Static layers (declared, or marked static by their local read) have no date axis: no history pass.
  await runStage('temporal', surfaces.filter((source) => !staticSources.has(source)).map((source) => ({
    source, tool: 'surface_evidence_for_selection', args: regionalSelectionArguments(payload, temporal, source),
  })), 15_000);
  for (const source of surfaces) {
    const line = regionalLaneLimitation(source, laneNotes.get(source) ?? []);
    if (line && evidence.limitations.length < 35) evidence.limitations.push(line);
  }
  evidence.stages[3].status = 'partial';
  evidence.limitations.push('Strategy screening identifies evidence requirements; it does not validate suitability, rank causal benefits, or establish a treatment rate.');
  evidence.limitations.push('All catalogue layers remain queryable, including hidden layers. Initial reads cover every theme (fire, drought, weather, water, soil, vegetation, climate) plus up to four selected layers; use additional reads for other relevant layers and history continuation.');
  if (temporal.viewedDates.length > 1) evidence.limitations.push('This is a mixed-time comparison. Each selected layer retains its own day; unselected layers inherit the latest selected comparison day.');
  return {
    catalogue, evidence, measurementFacts, siteFactObservations,
    context: JSON.stringify({ selection: temporal.analysisSelection, availableLayers: catalogue.surfaces, evidence, observations: results, measurementFacts, strategyScreening: STRATEGY_SCREENING }),
  };
}
