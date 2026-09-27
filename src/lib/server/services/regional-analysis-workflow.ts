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
import { analysisDateRange, DEFAULT_ANALYSIS_WINDOW } from '@/lib/regional-analysis-selection';
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
type ReadRequest = { tool: string; source?: string; args: Record<string, unknown> };
type EvidenceRead = { id: string; evidenceReadId: string; evidenceSource?: string; evidenceStatus: AuditCall['status']; result: unknown };

export const REGIONAL_ANALYSIS_STAGES = [
  ['inventory', 'Discover environmental sources'],
  ['local', 'Read local conditions'],
  ['temporal', 'Compare historical conditions'],
  ['strategies', 'Screen management alternatives'],
] as const;

export const REGIONAL_ANALYSIS_PRIORITY_SURFACES = [
  'soil-field-vpd', 'vegetation', 'climate-field-precipitation',
  'soil-field-moisture', 'soil-field-temperature', 'soil-survey', 'watersheds', 'water-gauges', 'drought-areas',
  'weather-observations', 'climate-field-precipitation', 'climate-field-soil-wetness-root-zone',
  'burn-severity', 'fire-detections', 'fire-perimeters',
];

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

/** Bind numeric tile reads to this request's current location and calendar selection. */
export function regionalSelectionArguments(
  payload: RegionalContextPayload, temporal: TemporalContext, source: string, history = true,
): Record<string, unknown> {
  const day = regionalEvidenceDay(temporal, source);
  const window = temporal.analysisSelection ?? DEFAULT_ANALYSIS_WINDOW;
  const range = history ? analysisDateRange(day, window.timeScale, window.rangeSteps)
    : { rangeStart: day, rangeEnd: day };
  return {
    surface_name: source, day,
    longitude: payload.location.lon, latitude: payload.location.lat,
    range_start: range.rangeStart, range_end: range.rangeEnd,
    time_scale: window.timeScale, zoom: temporal.analysisSelection?.zoom ?? 13,
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
    return { ...args, ...regionalSelectionArguments(payload, temporal, source) };
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
  if (root.error || root.refusal_code) return { status: 'refused', reason: String(root.error ?? root.refusal_code).slice(0, 240) };
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
    ...evidenceResultStatus(result, tool),
  };
}

/** Describe requested-day availability without turning missing or unchecked dates into observations. */
export function regionalEvidenceLimitations(audit: AuditCall, result: unknown): string[] {
  if (audit.tool !== 'surface_evidence_for_selection' || !audit.source) return [];
  const root = object(result);
  if (!root) return [];
  const history = object(root.history);
  const lanes = Array.isArray(root.lanes) ? root.lanes.map(object).filter((lane) => lane !== null) : [];
  const selected = lanes.map((lane) => object(lane.selected)).filter((entry) => entry !== null);
  const historyEntries = lanes.flatMap((lane) => Array.isArray(lane.history) ? lane.history.map(object).filter((entry) => entry !== null) : []);
  const hasRecords = (entry: Record<string, unknown>) => ['features', 'rows'].some((field) => Array.isArray(entry[field]) && entry[field].length > 0);
  const emptySelected = selected.filter((entry) => !hasRecords(entry));
  const emptyHistory = historyEntries.filter((entry) => !hasRecords(entry));
  if (!root.error && !root.refusal_code && emptySelected.length === 0 && emptyHistory.length === 0 && history?.complete !== false) return [];
  const details = [`${audit.source} [${audit.id}]: availability only, not a measured condition.`];
  if (selected.length) {
    details.push(`Selected request ${audit.selectedDate ?? 'date unspecified'}: ${selected.length - emptySelected.length}/${selected.length} lane responses contain measurement records.`);
  } else details.push('No selected-day measurement envelope was returned.');
  if (emptySelected.length) {
    const states = [...new Set(emptySelected.map((entry) => typeof entry.state === 'string' ? entry.state.slice(0, 80) : 'state not reported'))];
    details.push(`Selected responses without records: ${states.join(', ')}.`);
  }
  if (history) {
    const days = Array.isArray(history.sampled_days) ? [...new Set(history.sampled_days.filter(isCalendarDay))].sort() : null;
    if (days) details.push(`Explicitly checked history calendar dates (${days.length}): ${days.slice(0, 31).join(', ') || 'none'}${days.length > 31 ? `; ${days.length - 31} further checked dates omitted here` : ''}.`);
    else details.push('This reader does not declare a checked history calendar-day list.');
    details.push(`History completeness reported by this response: ${history.complete === true ? 'complete' : history.complete === false ? 'incomplete' : 'unknown'}.`);
    if (emptyHistory.length) {
      details.push(`${emptyHistory.length}/${historyEntries.length} history lane responses contained no measurement records.`);
      const states = new Map<string, Set<string>>();
      for (const entry of emptyHistory) {
        if (!days || !isCalendarDay(entry.requested_day) || !days.includes(entry.requested_day)) continue;
        const state = typeof entry.state === 'string' ? entry.state.slice(0, 80) : 'state not reported';
        if (!states.has(state) && states.size < 4) states.set(state, new Set());
        states.get(state)?.add(entry.requested_day);
      }
      for (const [state, dates] of states) {
        const listed = [...dates].sort();
        details.push(`${state} at checked requests: ${listed.slice(0, 5).join(', ')}${listed.length > 5 ? `; ${listed.length - 5} further dates omitted here` : ''}.`);
      }
    }
    if (typeof history.next_page_start === 'number' && Number.isSafeInteger(history.next_page_start) && history.next_page_start >= 0) details.push(`Continuation page_start: ${history.next_page_start}.`);
    if (typeof history.state === 'string') details.push(`History state: ${history.state.slice(0, 80)}.`);
  }
  const caveat = ' Unchecked dates remain unknown. No returned records is not evidence of environmental absence or of unavailability across the entire requested window.';
  return [details.join(' ').slice(0, 2_000 - caveat.length) + caveat];
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
      'History pages sample the entire requested window. Sampling, omitted days and continuation must be reported; samples do not establish a continuous trend or seasonal baseline.',
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
      await Promise.all(Array.from({ length: 3 }, async () => {
        while (next < requests.length) {
          const request = requests[next++];
          const id = `${stage}-${next}`;
          const base = { ...regionalEvidenceAuditCall(id, stage, request.tool, request.args, { error: 'read_not_executed' }),
            ...(request.source ? { source: request.source } : {}) };
          if (controller.signal.aborted || !tools.has(request.tool)) {
            entries.push({ ...base, status: 'not_queried', reason: controller.signal.aborted ? 'The reserved stage time budget was exhausted.' : 'The deployed catalogue does not expose this reader.' });
            continue;
          }
          try {
            const raw = await callRegionalEvidenceTool(request.tool, request.args, controller.signal);
            const result: unknown = JSON.parse(raw);
            const audit = { ...regionalEvidenceAuditCall(id, stage, request.tool, request.args, result), ...(request.source ? { source: request.source } : {}) };
            entries.push(audit);
            evidence.limitations.push(...regionalEvidenceLimitations(audit, result).slice(0, Math.max(0, 35 - evidence.limitations.length)));
            const readFacts = regionalFactsForRead(audit, result);
            measurementFacts.facts.push(...readFacts.facts);
            measurementFacts.omittedFacts += readFacts.omittedFacts;
            siteFactObservations.push(...siteFactObservationsForRead(audit, result));
            if (audit.status === 'observed' && readFacts.facts.length === 0 && evidence.limitations.length < 35) {
              evidence.limitations.push(`${audit.source ?? request.tool} [${id}]: returned records contained no renderable measurement facts; inspect the raw source before making a measured-condition claim.`);
            }
            results.push({ id, evidenceReadId: id, evidenceSource: audit.source, evidenceStatus: audit.status, result: boundedEvidence(result) });
          } catch (error) {
            if (signal?.aborted) throw error;
            entries.push({ ...base, status: 'error', reason: controller.signal.aborted ? 'The evidence read exceeded its reserved stage deadline.' : 'The environmental tool read failed.' });
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
  const surfaces = [...new Set([...selectedSurfaces, ...REGIONAL_ANALYSIS_PRIORITY_SURFACES])]
    .filter((source) => catalogue.surfaces.includes(source)).slice(0, 6);
  await runStage('local', surfaces.map((source) => ({
    source,
    tool: 'surface_evidence_for_selection',
    args: regionalSelectionArguments(payload, temporal, source, false),
  })), 12_000);
  await runStage('temporal', surfaces.map((source) => ({
    source, tool: 'surface_evidence_for_selection', args: regionalSelectionArguments(payload, temporal, source),
  })), 15_000);
  evidence.stages[3].status = 'partial';
  evidence.limitations.push('Strategy screening identifies evidence requirements; it does not validate suitability, rank causal benefits, or establish a treatment rate.');
  evidence.limitations.push('All catalogue layers remain queryable, including hidden layers. Initial reads prioritize six selected or environmental context layers; use additional reads for other relevant layers and history continuation.');
  if (temporal.viewedDates.length > 1) evidence.limitations.push('This is a mixed-time comparison. Each selected layer retains its own day; unselected layers inherit the latest selected comparison day.');
  return {
    catalogue, evidence, measurementFacts, siteFactObservations,
    context: JSON.stringify({ selection: temporal.analysisSelection, availableLayers: catalogue.surfaces, evidence, observations: results, measurementFacts, strategyScreening: STRATEGY_SCREENING }),
  };
}
