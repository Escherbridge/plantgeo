import type { RegionalAnalysisEvidence } from '@/lib/regional-intelligence';
import { isLayerToggleId, LAYER_REGISTRY } from '@/lib/map/layer-registry';
import { analysisDateRange, DEFAULT_ANALYSIS_WINDOW } from '@/lib/regional-analysis-selection';
import { selectionTile } from './regional-map-evidence';
import type { RegionalContextPayload, TemporalContext } from './regional-context';
import {
  callRegionalEvidenceTool,
  loadRegionalEvidenceTools,
  type RegionalEvidenceCatalogue,
} from './regional-evidence-tools';

type AuditCall = RegionalAnalysisEvidence['toolCalls'][number];
type ReadRequest = { tool: string; source?: string; args: Record<string, unknown> };
type EvidenceRead = { id: string; result: unknown };

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

export function bindRegionalEvidenceArguments(
  tool: string, args: Record<string, unknown>, payload: RegionalContextPayload, temporal: TemporalContext,
): Record<string, unknown> {
  const source = typeof args.surface_name === 'string' ? regionalSurfaceName(args.surface_name)
    : tool === 'drought_history_at_point' ? 'drought-areas' : tool === 'fire_history_near_point' ? 'burn-severity' : '';
  if (tool === 'surface_evidence_for_selection') {
    return { ...args, ...regionalSelectionArguments(payload, temporal, source) };
  }
  const day = regionalEvidenceDay(temporal, source);
  const tile = 'bbox' in args ? selectionTile(payload.location.lon, payload.location.lat, temporal.analysisSelection?.zoom ?? 13) : null;
  return {
    ...args,
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

/** Classify evidence only from returned rows and explicit serving states. */
export function evidenceResultStatus(value: unknown): Pick<AuditCall, 'status' | 'summary' | 'reason'> {
  const root = object(value);
  if (!root) return { status: 'error', reason: 'The tool returned an invalid evidence object.' };
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

/** Keep bounded rows with their provenance; omitted entries are explicitly counted. */
export function boundedEvidence(value: unknown, depth = 0, field = ''): unknown {
  if (depth > 12) return { omitted: 'Nested detail exceeds the evidence display bound.' };
  if (typeof value === 'string') return value.length > 2_000 ? `${value.slice(0, 2_000)} [text truncated]` : value;
  if (Array.isArray(value)) {
    const limit = field === 'weekly_severity' ? 120 : ['history', 'sampled_days'].includes(field) ? 31 : 8;
    const entries = value.slice(0, limit).map((entry) => boundedEvidence(entry, depth + 1));
    return value.length > limit ? { entries, omittedEntries: value.length - limit } : entries;
  }
  if (object(value)) return Object.fromEntries(Object.entries(value as Record<string, unknown>).map(([key, entry]) => [key, boundedEvidence(entry, depth + 1, key)]));
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
      if (['valid_date', 'observed_day', 'served_day'].includes(key) && isCalendarDay(entry)) {
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
  const selectedDate = args.day ?? args.as_of_day;
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
    : tool === 'drought_history_at_point' ? 'drought-areas' : '';
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
    ...evidenceResultStatus(result),
  };
}

export interface RegionalAnalysisWorkflow {
  catalogue: RegionalEvidenceCatalogue | null;
  evidence: RegionalAnalysisEvidence;
  context: string;
}

/** Derive a stage result from all of its completed and attempted reads. */
export function regionalEvidenceStageStatus(entries: AuditCall[]): RegionalAnalysisEvidence['stages'][number]['status'] {
  const admissible = (entry: AuditCall) => ['observed', 'governed_absence'].includes(entry.status);
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
  try { catalogue = await loadRegionalEvidenceTools(signal); }
  catch (error) { if (signal?.aborted) throw error; }
  if (!catalogue) {
    evidence.limitations.push('The environmental tool catalogue could not be loaded. Only the supplied regional context was available; historical and regional comparisons were not performed.');
    return { catalogue, evidence, context: JSON.stringify({ evidence, strategyScreening: STRATEGY_SCREENING }) };
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
            entries.push({ ...regionalEvidenceAuditCall(id, stage, request.tool, request.args, result), ...(request.source ? { source: request.source } : {}) });
            results.push({ id, result: boundedEvidence(result) });
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
    catalogue, evidence,
    context: JSON.stringify({ selection: temporal.analysisSelection, availableLayers: catalogue.surfaces, evidence, observations: results, strategyScreening: STRATEGY_SCREENING }),
  };
}
