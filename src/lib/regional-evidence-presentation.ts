/**
 * View-model for a regional analysis report: the ONE shape both the panel and the Markdown export
 * render, so screen and export cannot drift. See `src/components/panels/AGENTS.md`
 * §"Regional report view-model".
 */
import {
  AI_GENERATED_DISCLAIMER,
  AI_GENERATED_LABEL,
  REGIONAL_TOOL_EVIDENCE_SOURCES,
  RETIRED_REGIONAL_TOOL_EVIDENCE_SOURCES,
  STRATEGY_KNOWLEDGE_EVIDENCE_SOURCE,
  isRegionalEvidenceSource,
  isStrategyKnowledgeTool,
  regionalEvidenceFreshnessState,
  regionalEvidencePublicationDay,
  regionalEvidenceSnapshotDay,
  type EvidenceOrigin,
  type LiteratureCitation,
  type RegionalAnalysisEvidence,
  type RegionalIntelligenceResponse,
} from "@/lib/regional-intelligence";

type EvidenceCheck = RegionalAnalysisEvidence["toolCalls"][number];

/** The only statuses a source row may show, best first. */
export const SOURCE_STATUSES = [
  "Found",
  "Nearest day",
  "Nearest cell",
  "Static layer",
  "Not published",
  "Error",
] as const;
export type SourceStatus = (typeof SOURCE_STATUSES)[number];

/** How many findings / recommendations are visible before "Show more". */
export const KEY_ITEM_LIMIT = 3;

export const REPORT_FOOTER_NOTE = "AI-generated; values are published estimates, not measurements.";
export const PARTIAL_NOTE = "Some sources unavailable — see Sources.";

export interface CitationView {
  recordId: string;
  title: string;
  effect: string | null;
  conditions: string | null;
  url: string | null;
}

export interface SourceRow {
  laneId: string;
  label: string;
  status: SourceStatus;
  /** The day the lane was actually read at, or null when nothing was read. */
  day: string | null;
  /** "6 d earlier" / "23.6 km away"; null when the read was exact. */
  dayNote: string | null;
  /** Calls merged into this row (local + history passes, repeated literature lookups). */
  callCount: number;
  /** Raw scope lines, shown only inside the row's expand. */
  details: string[];
  citations: CitationView[];
}

export interface GapLine {
  laneId: string;
  label: string;
  text: string;
}

export interface FindingView {
  text: string;
  /** "Published estimate · 2026-09-04 · nearest day, 6 d earlier"; null when nothing to say. */
  meta: string | null;
  groundingNote: string | null;
}

export interface RecommendationView {
  title: string;
  timeframe: string;
  rationale: string;
  groundingNote: string | null;
}

export interface SourcesView {
  rows: SourceRow[];
  gaps: GapLine[];
  caveats: string[];
  webSources: { title: string; url: string }[];
  isPartial: boolean;
}

export interface ReportView {
  risk: { level: string; label: string; headline: string };
  findings: FindingView[];
  recommendations: RecommendationView[];
  consult: string | null;
  sources: SourcesView;
}

/** Thrown when a payload lacks the fields a report cannot be drawn without; caught by the report boundary. */
export class MalformedReportError extends Error {
  constructor(detail: string) {
    super(`Malformed regional analysis report: ${detail}`);
    this.name = "MalformedReportError";
  }
}

// ---------------------------------------------------------------------------
// Formatting primitives
// ---------------------------------------------------------------------------

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function arrayOf<T>(value: unknown): T[] {
  return Array.isArray(value) ? (value as T[]) : [];
}

function nonEmptyString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function humanize(value: string): string {
  return value.replace(/[_-]/g, " ");
}

function capitalize(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1);
}

/** camelCase initial-context keys and kebab tool sources share one lane id ("firePerimeters" = "fire-perimeters"). */
export function laneIdFor(source: string): string {
  return source.replace(/([a-z0-9])([A-Z])/g, "$1-$2").toLowerCase();
}

const LANE_LABELS: Record<string, string> = {
  [STRATEGY_KNOWLEDGE_EVIDENCE_SOURCE]: "Strategy literature",
  "mtbs-perimeters": "MTBS perimeters",
};

export function laneLabel(laneId: string): string {
  return LANE_LABELS[laneId] ?? capitalize(humanize(laneId));
}

export function formatZoom(zoom: number): string {
  return `Zoom ${zoom.toFixed(1)}`;
}

export function formatCoordinates(location: { lat: number; lon: number }): string {
  return `${location.lat.toFixed(4)}°, ${location.lon.toFixed(4)}°`;
}

function finiteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function dayOffsetText(dayOffset: number): string {
  return `${Math.abs(dayOffset)} d ${dayOffset < 0 ? "earlier" : "later"}`;
}

function cellDistanceText(km: number): string {
  return `${km.toFixed(1)} km`;
}

/** "an agronomist", "a soil scientist": the article follows the first word's sound, approximated by its letter. */
export function withIndefiniteArticle(noun: string): string {
  return `${/^[aeiou]/i.test(noun) ? "an" : "a"} ${noun}`;
}

/** "Confirm with an agronomist or soil scientist before acting." from the recommendations' disciplines. */
export function consultLine(disciplines: readonly string[]): string | null {
  const names = [...new Set(disciplines.filter((discipline) => typeof discipline === "string").map(humanize))];
  if (!names.length) return null;
  const [first, ...rest] = names;
  const head = withIndefiniteArticle(first);
  const list =
    rest.length === 0
      ? head
      : rest.length === 1
        ? `${head} or ${rest[0]}`
        : `${[head, ...rest.slice(0, -1)].join(", ")}, or ${rest[rest.length - 1]}`;
  return `Confirm with ${list} before acting.`;
}

/** Only an absolute https link is ever rendered, even from an older saved report. */
export function httpsSourceUrl(value: string | undefined): string | null {
  if (!value) return null;
  try {
    return new URL(value).protocol === "https:" ? value : null;
  } catch {
    return null;
  }
}

/** Escapes Markdown syntax in cited free text; an intraword underscore ("plot_1") stays bare (CommonMark never emphasises it). */
export function escapeMarkdown(text: string): string {
  return text.replace(/([\\`*_{}[\]()#+!|<>~])/g, (_match, char: string, offset: number, source: string) => {
    if (char === "_") {
      const before = source[offset - 1];
      const after = source[offset + 1];
      if (before && after && /[A-Za-z0-9]/.test(before) && /[A-Za-z0-9]/.test(after)) return char;
    }
    return `\\${char}`;
  });
}

/** Percent-encodes parentheses so a cited URL cannot truncate a Markdown `[Source](url)` link early. */
function markdownLinkUrl(url: string): string {
  return url.replace(/\(/g, "%28").replace(/\)/g, "%29");
}

// ---------------------------------------------------------------------------
// Evidence checks -> source rows
// ---------------------------------------------------------------------------

function checkLaneIds(check: EvidenceCheck): string[] {
  if (isStrategyKnowledgeTool(check.tool) || check.source === STRATEGY_KNOWLEDGE_EVIDENCE_SOURCE) {
    return [STRATEGY_KNOWLEDGE_EVIDENCE_SOURCE];
  }
  const sources = arrayOf<string>(check.sources).filter((source) => typeof source === "string");
  const named = sources.length ? sources : [check.source ?? check.tool];
  return [...new Set(named.filter(Boolean).map(laneIdFor))];
}

/** One call's status; null for a call that was never made. */
function checkStatus(check: EvidenceCheck): SourceStatus | null {
  switch (check.status) {
    case "not_queried":
      return null;
    case "error":
    case "refused":
      return "Error";
    case "unavailable":
    case "answered_no_records":
      return "Not published";
    default:
      if (check.staticLayer) return "Static layer";
      if ((finiteNumber(check.cellDistanceKm) ?? 0) > 0) return "Nearest cell";
      if ((finiteNumber(check.dayOffset) ?? 0) !== 0) return "Nearest day";
      return "Found";
  }
}

function isFailedCheck(check: EvidenceCheck): boolean {
  return check.status === "unavailable" || check.status === "refused" || check.status === "error";
}

/** The day a check actually read; older saved reports predate `resolvedDay` and fall back to served/observed days. */
function checkResolvedDay(check: EvidenceCheck): string | null {
  if (isFailedCheck(check) || check.status === "answered_no_records" || check.staticLayer) return null;
  const last = (days: unknown) => {
    const list = arrayOf<string>(days);
    return list.length ? list[list.length - 1] : null;
  };
  return check.resolvedDay ?? last(check.servedDates) ?? last(check.observedDates) ?? check.selectedDate ?? null;
}

function checkDayNote(check: EvidenceCheck): string | null {
  const dayOffset = finiteNumber(check.dayOffset);
  const cellDistanceKm = finiteNumber(check.cellDistanceKm);
  const notes = [
    dayOffset ? dayOffsetText(dayOffset) : null,
    cellDistanceKm && cellDistanceKm > 0 ? `${cellDistanceText(cellDistanceKm)} away` : null,
  ].filter((note): note is string => note !== null);
  return notes.length ? notes.join(", ") : null;
}

function checkScope(check: EvidenceCheck, stageLabel: string | null): string {
  const zoom = finiteNumber(check.zoom);
  const location =
    isRecord(check.location) && finiteNumber(check.location.lat) !== null && finiteNumber(check.location.lon) !== null
      ? formatCoordinates(check.location)
      : null;
  const scope = [
    check.selectedDate ? `Requested ${check.selectedDate}` : null,
    check.rangeStart && check.rangeEnd ? `Window ${check.rangeStart} – ${check.rangeEnd} (${check.timeScale ?? "day"})` : null,
    zoom !== null ? formatZoom(zoom) : null,
    location,
  ].filter(Boolean).join(" · ");
  return [stageLabel, scope].filter(Boolean).join(": ");
}

/** `"<source> [<id>]: text"`, the prefix the server stamps on a per-read limitation. */
const ATTRIBUTED_LIMITATION = /^(\S+) \[([^\]]+)\]: ([\s\S]+)$/;

function citationView(citation: LiteratureCitation): CitationView {
  const direction = citation.direction ? `direction: ${humanize(citation.direction)}` : null;
  const effect = citation.magnitude
    ? `Reported ${citation.magnitude}${direction ? ` (${direction})` : ""}`
    : direction
      ? `Reported ${direction}`
      : null;
  return {
    recordId: citation.recordId,
    title: citation.title,
    effect,
    conditions: citation.conditions ?? null,
    url: httpsSourceUrl(citation.sourceUrl),
  };
}

/** Initial-context sources that are deferred model placeholders, not data; hidden while they say nothing. */
const DEFERRED_PLACEHOLDER_SOURCES = new Set(["strategyRecommendations", "carbonPotential"]);

function freshnessEntry(source: string, value: string, now: number): { status: SourceStatus; day: string | null; detail: string } | null {
  if (DEFERRED_PLACEHOLDER_SOURCES.has(source) && (value === "unavailable" || value === "published_revision_required")) return null;
  const state = isRegionalEvidenceSource(source) ? regionalEvidenceFreshnessState(source, value, now) : "unavailable";
  if (state === "unavailable") return { status: "Not published", day: null, detail: "Initial context: no dated evidence" };
  if (state === "pending") return { status: "Not published", day: null, detail: "Initial context: awaiting validated publication" };
  if (value === "static_release_untimed") return { status: "Static layer", day: null, detail: "Initial context: static release (undated)" };
  const snapshotDay = isRegionalEvidenceSource(source) ? regionalEvidenceSnapshotDay(source, value) : null;
  const publicationDay = isRegionalEvidenceSource(source) ? regionalEvidencePublicationDay(source, value) : null;
  // The drought release is a publisher calendar day at synthetic UTC midnight: never localise it.
  const timestamp = Date.parse(value);
  const day =
    snapshotDay ??
    publicationDay ??
    (source === "drought" ? value.slice(0, 10) : new Date(timestamp).toLocaleString(undefined, { timeZoneName: "short" }));
  const kind = snapshotDay ? "snapshot captured" : publicationDay ? "publication available" : "latest reading";
  return {
    status: "Found",
    day: state === "stale" ? `${day} (stale)` : day,
    detail: `Initial context: ${kind}${state === "stale" ? ", older than this source's freshness limit" : ""}`,
  };
}

interface RowDraft extends SourceRow {
  rank: number;
  gap: string | null;
}

/** Groups evidence checks by lane (local + history merge; repeated literature lookups merge as ×N). */
export function buildSourcesView(input: {
  evidence?: RegionalAnalysisEvidence | null;
  freshness?: Record<string, string> | null;
  citations?: LiteratureCitation[];
  webSources?: { title: string; url: string }[];
  now?: number;
}): SourcesView {
  const evidence = isRecord(input.evidence) ? input.evidence : null;
  const stages = arrayOf<RegionalAnalysisEvidence["stages"][number]>(evidence?.stages);
  const calls = arrayOf<EvidenceCheck>(evidence?.toolCalls).filter(
    (check) => isRecord(check) && typeof check.status === "string" && typeof check.tool === "string",
  );
  const rows = new Map<string, RowDraft>();
  const callLane = new Map<string, string>();

  const rowFor = (laneId: string): RowDraft => {
    let row = rows.get(laneId);
    if (!row) {
      row = {
        laneId, label: laneLabel(laneId), status: "Error", rank: Number.POSITIVE_INFINITY,
        day: null, dayNote: null, callCount: 0, details: [], citations: [], gap: null,
      };
      rows.set(laneId, row);
    }
    return row;
  };
  const addDetail = (row: RowDraft, line: string | null | undefined) => {
    if (line && !row.details.includes(line)) row.details.push(line);
  };

  for (const check of calls) {
    const status = checkStatus(check);
    if (status === null) continue;
    const stageLabel = stages.find((stage) => stage?.id === check.stage)?.label ?? null;
    for (const laneId of checkLaneIds(check)) {
      callLane.set(check.id, laneId);
      const row = rowFor(laneId);
      row.callCount += 1;
      const rank = SOURCE_STATUSES.indexOf(status);
      if (rank < row.rank) {
        row.rank = rank;
        row.status = status;
        row.day = checkResolvedDay(check);
        row.dayNote = checkDayNote(check);
      }
      addDetail(row, checkScope(check, stageLabel));
      addDetail(row, check.summary);
      if (check.reason) addDetail(row, humanize(check.reason));
      if (isFailedCheck(check) && row.gap === null) row.gap = humanize(check.reason ?? check.summary ?? "no data for the requested day");
    }
  }

  const caveats: string[] = [];
  for (const limitation of arrayOf<string>(evidence?.limitations)) {
    if (typeof limitation !== "string" || !limitation.trim()) continue;
    const match = ATTRIBUTED_LIMITATION.exec(limitation.trim());
    const laneId = match ? callLane.get(match[2]) ?? (rows.has(laneIdFor(match[1])) ? laneIdFor(match[1]) : null) : null;
    if (match && laneId) {
      const row = rowFor(laneId);
      if (row.gap === null) row.gap = match[3];
      else addDetail(row, match[3]);
      continue;
    }
    if (!caveats.includes(limitation.trim())) caveats.push(limitation.trim());
  }

  const now = input.now ?? Date.now();
  for (const [source, value] of Object.entries(isRecord(input.freshness) ? input.freshness : {})) {
    if (typeof value !== "string") continue;
    const entry = freshnessEntry(source, value, now);
    if (!entry) continue;
    const laneId = laneIdFor(source);
    const existing = rows.get(laneId);
    if (existing) {
      addDetail(existing, `${entry.detail}${entry.day ? ` ${entry.day}` : ""}`);
      continue;
    }
    const row = rowFor(laneId);
    row.rank = SOURCE_STATUSES.indexOf(entry.status);
    row.status = entry.status;
    row.day = entry.day;
    addDetail(row, entry.detail);
  }

  const citations = new Map<string, CitationView>();
  for (const citation of input.citations ?? []) {
    if (isRecord(citation) && typeof citation.recordId === "string" && typeof citation.title === "string") {
      citations.set(citation.recordId, citationView(citation));
    }
  }
  if (citations.size) {
    const row = rowFor(STRATEGY_KNOWLEDGE_EVIDENCE_SOURCE);
    if (row.rank === Number.POSITIVE_INFINITY) {
      row.rank = 0;
      row.status = "Found";
    }
    row.citations = [...citations.values()];
  }

  const drafts = [...rows.values()];
  const gaps = drafts
    .filter((row) => row.gap !== null)
    .map((row) => ({ laneId: row.laneId, label: row.label, text: row.gap as string }));
  return {
    rows: drafts.map(({ rank: _rank, gap: _gap, ...row }) => row),
    gaps,
    caveats,
    webSources: arrayOf<{ title: string; url: string }>(input.webSources).filter(
      (source) => isRecord(source) && typeof source.title === "string" && httpsOrHttp(source.url),
    ),
    isPartial: gaps.length > 0,
  };
}

function httpsOrHttp(value: unknown): boolean {
  if (typeof value !== "string") return false;
  try {
    return ["https:", "http:"].includes(new URL(value).protocol);
  } catch {
    return false;
  }
}

/** "Soil field moisture · Nearest day · 2026-09-04 (6 d earlier)": the row line, identical on screen and in Markdown. */
export function sourceRowSummary(row: SourceRow): string {
  const label = row.callCount > 1 && row.laneId === STRATEGY_KNOWLEDGE_EVIDENCE_SOURCE ? `${row.label} ×${row.callCount}` : row.label;
  const day = row.day ? `${row.day}${row.dayNote ? ` (${row.dayNote})` : ""}` : null;
  return [label, row.status, day].filter(Boolean).join(" · ");
}

export function citationSummary(citation: CitationView, escape: (text: string) => string = (text) => text): string {
  return [
    escape(citation.title),
    citation.effect ? escape(citation.effect) : null,
    citation.conditions ? `Conditions: ${escape(citation.conditions)}` : null,
  ].filter(Boolean).join(" · ");
}

// ---------------------------------------------------------------------------
// Report -> view
// ---------------------------------------------------------------------------

const RISK_LABELS: Record<string, string> = { low: "Low", moderate: "Moderate", high: "High", critical: "Critical" };
const TIMEFRAME_LABELS: Record<string, string> = { immediate: "Now", short_term: "This season", long_term: "Multi-year" };

function originLabel(origin: EvidenceOrigin, source: string | undefined): string | null {
  if (origin === "model_inference") return null;
  if (origin === "literature") return "Literature";
  if (origin === "web") return "Web source";
  if (source === "soilProperties" || source?.startsWith("climate-field-") || source?.startsWith("soil-field-")) return "Published estimate";
  const published: readonly string[] = [...REGIONAL_TOOL_EVIDENCE_SOURCES, ...RETIRED_REGIONAL_TOOL_EVIDENCE_SOURCES];
  return source && published.includes(source) ? "Published data" : "Observed data";
}

/** "value · day": the cited read's day, plus "nearest day/cell" only when the read was not exact. */
function findingMeta(
  observation: RegionalIntelligenceResponse["observations"][number],
  calls: EvidenceCheck[],
): string | null {
  const cited =
    observation.evidenceOrigin === "warehouse"
      ? arrayOf<string>(observation.evidenceReadIds)
          .map((id) => calls.find((check) => check.id === id))
          .find((check): check is EvidenceCheck => check !== undefined && checkStatus(check) !== null)
      : undefined;
  const dayOffset = finiteNumber(cited?.dayOffset);
  const cellDistanceKm = finiteNumber(cited?.cellDistanceKm);
  return [
    originLabel(observation.evidenceOrigin, observation.evidenceSource),
    cited ? checkResolvedDay(cited) : null,
    dayOffset ? `nearest day, ${dayOffsetText(dayOffset)}` : null,
    cellDistanceKm && cellDistanceKm > 0 ? `nearest cell, ${cellDistanceText(cellDistanceKm)}` : null,
  ].filter(Boolean).join(" · ") || null;
}

export function buildReportView(response: RegionalIntelligenceResponse, now?: number): ReportView {
  if (!isRecord(response)) throw new MalformedReportError("not an object");
  const risk = response.riskSummary as unknown;
  if (!isRecord(risk) || typeof risk.headline !== "string") throw new MalformedReportError("riskSummary.headline is missing");
  const level = typeof risk.level === "string" ? risk.level : "unknown";
  const evidence = isRecord(response.analysisEvidence) ? response.analysisEvidence : undefined;
  const calls = arrayOf<EvidenceCheck>(evidence?.toolCalls).filter(isRecord);

  const observations = arrayOf<RegionalIntelligenceResponse["observations"][number]>(response.observations).filter(
    (item) => isRecord(item) && typeof item.statement === "string",
  );
  const remediation = arrayOf<RegionalIntelligenceResponse["remediation"][number]>(response.remediation).filter(
    (item) => isRecord(item) && typeof item.title === "string",
  );
  const citations = [...observations, ...remediation]
    .filter((item) => item.evidenceOrigin === "literature")
    .flatMap((item) => arrayOf<LiteratureCitation>(item.literatureCitations));

  return {
    risk: { level, label: RISK_LABELS[level] ?? "Unknown", headline: risk.headline },
    findings: observations.map((observation) => ({
      text: observation.statement,
      meta: findingMeta(observation, calls),
      groundingNote: nonEmptyString(observation.groundingNote),
    })),
    recommendations: remediation.map((item) => ({
      title: item.title,
      timeframe: TIMEFRAME_LABELS[item.timeframe] ?? humanize(String(item.timeframe ?? "")),
      rationale: typeof item.rationale === "string" ? item.rationale : "",
      groundingNote: nonEmptyString(item.groundingNote),
    })),
    consult:
      consultLine(remediation.flatMap((item) => arrayOf<string>(item.consultProfessionals))) ??
      nonEmptyString(response.professionalConsultation),
    sources: buildSourcesView({
      evidence,
      freshness: isRecord(response.dataFreshness) ? (response.dataFreshness as Record<string, string>) : null,
      citations,
      webSources: arrayOf(response.webSources),
      now,
    }),
  };
}

export const NOT_GROUNDED_LABEL = "Not grounded in the cited research";
export const NO_RECOMMENDATION_TEXT =
  "The assistant did not find enough here to suggest a remediation strategy. Treat this as an absence of evidence, not an all-clear.";

export function recommendationLine(item: RecommendationView): string {
  return `${item.title} · ${item.timeframe}`;
}

/** Markdown for a view; the export never reads the raw response, only the view the screen draws. */
export function reportViewToMarkdown(view: ReportView): string {
  const lines: string[] = [`# Regional analysis — ${AI_GENERATED_LABEL}`, "", AI_GENERATED_DISCLAIMER, ""];
  lines.push(`## Risk: ${view.risk.label}`, view.risk.headline);
  if (view.sources.isPartial) lines.push("", `> ${PARTIAL_NOTE}`);
  if (view.findings.length) {
    lines.push("", "## Key findings");
    for (const finding of view.findings) {
      lines.push(`- ${finding.text}${finding.meta ? ` — ${finding.meta}` : ""}`);
      if (finding.groundingNote) lines.push(`  _${NOT_GROUNDED_LABEL}: ${escapeMarkdown(finding.groundingNote)}_`);
    }
  }
  lines.push("", "## Recommendations");
  if (view.recommendations.length) {
    for (const item of view.recommendations) {
      lines.push(`- **${recommendationLine(item)}** — ${item.rationale}`);
      if (item.groundingNote) lines.push(`  _${NOT_GROUNDED_LABEL}: ${escapeMarkdown(item.groundingNote)}_`);
    }
  } else {
    lines.push(NO_RECOMMENDATION_TEXT);
  }
  if (view.consult) lines.push("", view.consult);
  const { rows, gaps, caveats, webSources } = view.sources;
  if (rows.length || gaps.length || caveats.length || webSources.length) {
    lines.push("", "## Sources");
    for (const row of rows) {
      lines.push(`- ${sourceRowSummary(row)}`);
      for (const detail of row.details) lines.push(`  - ${detail}`);
      for (const citation of row.citations) {
        lines.push(`  - Literature: ${citationSummary(citation, escapeMarkdown)}${citation.url ? ` · [Source](${markdownLinkUrl(citation.url)})` : ""}`);
      }
    }
    if (gaps.length) {
      lines.push("", "### Gaps");
      for (const gap of gaps) lines.push(`- ${gap.label}: ${gap.text}`);
    }
    if (caveats.length) {
      lines.push("", "### Caveats");
      for (const caveat of caveats) lines.push(`- ${caveat}`);
    }
    if (webSources.length) {
      lines.push("", "### Web sources");
      for (const source of webSources) lines.push(`- [${escapeMarkdown(source.title)}](${markdownLinkUrl(source.url)})`);
    }
  }
  lines.push("", `_${REPORT_FOOTER_NOTE}_`);
  return lines.join("\n");
}

/** Renders the report as Markdown through the shared view-model. */
export function reportToMarkdown(response: RegionalIntelligenceResponse): string {
  return reportViewToMarkdown(buildReportView(response));
}
