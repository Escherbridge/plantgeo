import { createHash } from "node:crypto";

export interface RegionalMeasurementFact {
  id: string;
  source: string;
  statement: string;
  evidenceReadIds: [string];
}

export interface RegionalMeasurementFacts {
  facts: RegionalMeasurementFact[];
  omittedFacts: number;
}

interface MeasurementRead {
  id: string;
  source: string;
  result: unknown;
}

type RecordValue = Record<string, unknown>;
interface Candidate {
  record: RecordValue;
  envelope: RecordValue;
  lane: string;
  group: string;
  selected: boolean;
  position: number;
}

const FACTS_PER_READ = 10;
const STATEMENT_LENGTH = 500;
const RECORD_COLLECTIONS = new Set(["features", "rows", "records"]);
const RECORD_STATES = new Set(["published", "observed", "detail", "aggregate", "ready", "ok", "current"]);
const METADATA_FIELDS = new Set([
  "allowed_client_exposure", "coverage_fraction", "observation_count", "physical_candidate_count",
  "coordinate_uncertainty_m", "distance_meters", "distance_days", "distance_basis", "spatial_relation",
  "covers_probe_point", "temporal_relation", "type", "geometry", "bbox", "support_bbox",
  "cell_latitude", "cell_longitude", "centroid_latitude", "centroid_longitude", "latitude", "longitude",
  "lat", "lon", "coordinates", "precision", "coordinate_quality", "quality_control", "precedence_contract",
  "source_parameter",
]);
const DATE_FIELDS = new Set([
  "observed_day", "observedDay", "observed_at", "observedAt", "newest_observed_at", "valid_date", "validDate",
  "served_day", "requested_day", "published_at", "snapshot_as_of", "event_interval", "observed_interval",
]);
const METRIC_FIELDS = new Set(["normalized_value", "normalized_unit", "signal_name", "metric_value", "metric_unit", "metric_name"]);

function object(value: unknown): RecordValue | null {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value as RecordValue : null;
}

function scalar(value: unknown): value is string | number | boolean {
  return typeof value === "string" || typeof value === "boolean" || (typeof value === "number" && Number.isFinite(value));
}

function opaqueField(key: string, value: unknown): boolean {
  return /(?:^|_)(?:id|ids|key|keys|hash|sha256|checksum|digest|url|uri|path|filename|ordinal|ordinals)(?:_|$)/i.test(key)
    || /(?:Id|Ids|Key|Keys|Hash|Url|Uri)$/.test(key)
    || /^(?:input_source_|selected_source_|selected_release_|source_manifest|lineage_)/.test(key)
    || (typeof value === "string" && /^(?:[a-f\d]{32,}|[a-f\d]{8}(?:-[a-f\d]{4}){3}-[a-f\d]{12}|https?:\/\/|s3:\/\/)/i.test(value));
}

function metricParts(properties: RecordValue): string[] {
  for (const [valueKey, unitKey, nameKey] of [
    ["normalized_value", "normalized_unit", "signal_name"], ["metric_value", "metric_unit", "metric_name"],
  ]) {
    if (!scalar(properties[valueKey])) continue;
    const fields = [nameKey, valueKey, unitKey].filter((key) => scalar(properties[key]) && !opaqueField(key, properties[key]));
    return [fields.map((key) => `${key}=${JSON.stringify(properties[key])}`).join(", ")];
  }
  return [];
}

function recordParts(record: RecordValue): string[] {
  const properties = object(record.properties) ?? record;
  const metrics = metricParts(properties);
  if (metrics.length === 0 && [...METRIC_FIELDS].some((key) => Object.hasOwn(properties, key))) return [];
  const parts: string[] = [];
  const visit = (value: RecordValue, prefix = "", depth = 0) => {
    for (const key of Object.keys(value).sort()) {
      const entry = value[key];
      if (METADATA_FIELDS.has(key) || DATE_FIELDS.has(key) || METRIC_FIELDS.has(key) || opaqueField(key, entry)) continue;
      const label = prefix ? `${prefix}.${key}` : key;
      if (scalar(entry)) parts.push(`${label}=${JSON.stringify(entry)}`);
      else if (object(entry) && depth < 2) visit(entry as RecordValue, label, depth + 1);
    }
  };
  visit(properties);
  return [...metrics, ...parts];
}

function calendarDay(value: unknown): value is string {
  return typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value)
    && Number.isFinite(Date.parse(`${value}T00:00:00Z`))
    && new Date(`${value}T00:00:00Z`).toISOString().slice(0, 10) === value;
}

function timestamp(value: unknown): value is string {
  return typeof value === "string" && /^\d{4}-\d{2}-\d{2}T/.test(value) && Number.isFinite(Date.parse(value));
}

function temporalParts(candidate: Candidate, root: RecordValue): string[] {
  const properties = object(candidate.record.properties) ?? candidate.record;
  const scopes = [properties, candidate.record, candidate.envelope];
  const day = scopes.flatMap((scope) => [scope.observed_day, scope.observedDay]).find(calendarDay);
  const interval = object(candidate.record.observed_interval) ?? object(properties.event_interval) ?? object(properties.observed_interval);
  const instant = scopes.flatMap((scope) => [scope.observed_at, scope.observedAt, scope.newest_observed_at]).find(timestamp);
  const valid = scopes.flatMap((scope) => [scope.valid_date, scope.validDate]).find(calendarDay);
  const served = [candidate.record.served_day, candidate.envelope.served_day].find(calendarDay);
  const parts = day ? [`Observed day ${day}`]
    : interval && calendarDay(interval.start) && calendarDay(interval.end)
      ? [`Observation interval ${interval.start} to ${interval.end}`]
      : instant ? [`Observation timestamp ${instant}`] : ["Observation date not provided"];
  if (valid) parts.push(`Valid day ${valid}`);
  if (served) parts.push(`Served day ${served}`);
  const snapshot = candidate.envelope.snapshot_as_of;
  if (timestamp(snapshot)) parts.push(`Snapshot as of ${snapshot}`);
  const publication = candidate.envelope.published_at ?? root.published_at;
  if (timestamp(publication)) parts.push(`Published at ${publication}`);
  return parts;
}

function spatialParts(candidate: Candidate, root: RecordValue): string[] {
  const feature = candidate.record;
  const parts = feature.covers_probe_point === true ? ["Source support contains selection"]
    : feature.covers_probe_point === false ? ["Spatial neighbor; does not contain selection"] : [];
  if (Array.isArray(feature.support_bbox) && feature.support_bbox.length === 4
    && feature.support_bbox.every((value) => typeof value === "number" && Number.isFinite(value))) {
    parts.push(`Support bbox ${JSON.stringify(feature.support_bbox)}`);
  }
  const selection = object(root.selection);
  const tile = object(selection?.tile);
  if (parts.length === 0 && tile && [tile.z, tile.x, tile.y].every((value) => typeof value === "number" && Number.isFinite(value))) {
    parts.push(`Record from selected tile ${tile.z}/${tile.x}/${tile.y}`);
  }
  return parts.length > 0 ? parts : ["Spatial support not provided"];
}

function collectCandidates(root: RecordValue): Candidate[] {
  const candidates: Candidate[] = [];
  const visit = (value: RecordValue, envelope: RecordValue, lane: string, group: string, selected: boolean, depth: number) => {
    if (depth > 12 || value.error || value.refusal_code || (typeof value.state === "string" && !RECORD_STATES.has(value.state))) return;
    const scope = { ...envelope, ...Object.fromEntries(Object.entries(value).filter(([key]) => DATE_FIELDS.has(key))) };
    const nextLane = typeof value.parquet_lane === "string" ? value.parquet_lane : lane;
    for (const [key, entry] of Object.entries(value)) {
      if (RECORD_COLLECTIONS.has(key)) {
        const entries = Array.isArray(entry) ? entry : object(entry)?.entries;
        if (Array.isArray(entries)) {
          for (const record of entries) {
            const row = object(record);
            if (row && !row.error && !row.refusal_code && row.allowed_client_exposure !== false
              && object(row.properties)?.allowed_client_exposure !== false) {
              candidates.push({ record: row, envelope: scope, lane: nextLane, group, selected, position: candidates.length });
            }
          }
        }
      } else if (key === "selected" && object(entry)) {
        visit(entry as RecordValue, scope, nextLane, group, true, depth + 1);
      } else if ((key === "lanes" || key === "history") && Array.isArray(entry)) {
        entry.forEach((child, index) => {
          if (object(child)) visit(child as RecordValue, scope, nextLane, key === "lanes" ? `${group}.${index}` : group, key === "history" ? false : selected, depth + 1);
        });
      }
    }
  };
  visit(root, {}, "", "root", true, 0);
  return candidates;
}

function renderStatement(source: string, candidate: Candidate, root: RecordValue): string | null {
  const fields = recordParts(candidate.record);
  if (fields.length === 0) return null;
  const heading = `${source}${candidate.lane && candidate.lane !== source ? ` (${candidate.lane})` : ""}: ${candidate.selected ? "selected record" : "sampled history record"}`;
  const context = [...temporalParts(candidate, root), ...spatialParts(candidate, root)];
  const suffix = ". Additional record fields omitted.";
  let statement = [heading, ...context].join("; ");
  if (statement.length + suffix.length >= STATEMENT_LENGTH) return null;
  let used = 0;
  for (const field of fields) {
    if (`${statement}; ${field}${suffix}`.length > STATEMENT_LENGTH) continue;
    statement += `; ${field}`;
    used += 1;
  }
  if (used === 0) return null;
  return `${statement}${used < fields.length ? suffix : "."}`;
}

/** Render exact serving-record facts; selection IDs cannot carry across changed evidence. */
export function buildRegionalMeasurementFacts(reads: readonly MeasurementRead[]): RegionalMeasurementFacts {
  const facts: RegionalMeasurementFact[] = [];
  let omittedFacts = 0;
  for (const read of reads) {
    const root = object(read.result);
    if (!root || !read.id || !read.source) continue;
    const groups = new Map<string, RegionalMeasurementFact[]>();
    const seen = new Set<string>();
    const candidates = collectCandidates(root).sort((left, right) => Number(right.selected) - Number(left.selected)
      || Number(right.record.covers_probe_point === true) - Number(left.record.covers_probe_point === true)
      || left.position - right.position);
    for (const candidate of candidates) {
      const statement = renderStatement(read.source, candidate, root);
      if (!statement) {
        if (recordParts(candidate.record).length > 0) omittedFacts += 1;
        continue;
      }
      if (seen.has(statement)) continue;
      seen.add(statement);
      const digest = createHash("sha256").update(JSON.stringify({ source: read.source, selection: root.selection,
        requestedDay: root.requested_day, lane: candidate.lane, envelope: candidate.envelope, record: candidate.record })).digest("hex").slice(0, 12);
      const fact: RegionalMeasurementFact = {
        id: `${read.id}:fact-${candidate.position + 1}:${digest}`, source: read.source, statement, evidenceReadIds: [read.id],
      };
      const group = `${candidate.selected ? "selected" : "history"}:${candidate.group}`;
      const values = groups.get(group) ?? [];
      values.push(fact);
      groups.set(group, values);
    }
    const selected = [...groups].filter(([key]) => key.startsWith("selected:"));
    const history = [...groups].filter(([key]) => key.startsWith("history:"));
    let retained = 0;
    const ordered = [...selected, ...history];
    for (let index = 0; ordered.some(([, entries]) => entries.length > index); index += 1) {
      for (const [, entries] of ordered) {
        if (!entries[index]) continue;
        if (retained < FACTS_PER_READ) { facts.push(entries[index]); retained += 1; }
        else omittedFacts += 1;
      }
    }
  }
  return { facts, omittedFacts };
}
