import type { ParquetBrowserReaderResult } from "./parquet-presentation";

export type ParquetDayPolicy =
  | "exact"
  | "release"
  | "snapshot"
  | "vegetation-window"
  | "live-observations"
  | "static";

export interface ParquetDayRequest {
  requestedDay: string | null | undefined;
  policy: ParquetDayPolicy;
  subject: string;
  today?: string;
  retainedRequest?: Omit<ParquetDayRequest, "retainedRequest">;
}

interface ParquetQuery {
  data: ParquetBrowserReaderResult<unknown> | undefined;
  isPlaceholderData?: boolean;
}

const DAY_MS = 86_400_000;

function dayNumber(day: string): number {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) return Number.NaN;
  const instant = Date.parse(`${day}T00:00:00Z`);
  return Number.isFinite(instant) && new Date(instant).toISOString().slice(0, 10) === day
    ? instant / DAY_MS
    : Number.NaN;
}

function maximumLag(policy: ParquetDayPolicy, requestedDay: string, today: string): number {
  switch (policy) {
    case "exact": return 0;
    case "release": return 14;
    case "snapshot":
    case "static": return Number.POSITIVE_INFINITY;
    case "vegetation-window": return 29;
    case "live-observations": return requestedDay === today ? 1 : 0;
  }
}

function isWithin(day: string, endpoint: string, maximumDays: number): boolean {
  const lag = dayNumber(endpoint) - dayNumber(day);
  return Number.isFinite(lag) && lag >= 0 && lag <= maximumDays;
}

/** Validate the selected-day response before any presenter or drawn-day publisher; see AGENTS.md. */
export function withParquetDayContract<Q extends ParquetQuery>(query: Q, request: ParquetDayRequest) {
  const result = query.data;
  let temporalNotice: string | null = null;
  let temporalRefused = false;
  let servedDate: string | undefined;
  let answeredDate: string | undefined;

  if (result !== undefined && "requestedDay" in result) {
    const context = query.isPlaceholderData ? request.retainedRequest ?? request : request;
    const today = context.today ?? new Date().toISOString().slice(0, 10);
    const expected = context.policy === "static" ? result.requestedDay : context.requestedDay ?? result.requestedDay;
    const lag = maximumLag(context.policy, expected, today);
    const isWindow = context.policy === "vegetation-window" || context.policy === "live-observations";
    const terminalLag = result.state !== "ready" && isWindow ? lag : 0;
    if (query.isPlaceholderData && request.retainedRequest === undefined) {
      temporalRefused = true;
      temporalNotice = `${request.subject}: the retained response has not been verified for its original requested day. The response is not shown.`;
    } else if (!isWithin(result.requestedDay, expected, terminalLag)) {
      temporalRefused = true;
      temporalNotice = `${request.subject}: response requested ${result.requestedDay}, but the selected day is ${expected}. The response is not shown.`;
    } else if ("servedDay" in result && (
      !isWithin(result.servedDay, expected, lag) ||
      !isWithin(result.servedDay, result.requestedDay, Number.POSITIVE_INFINITY)
    )) {
      temporalRefused = true;
      temporalNotice = `${request.subject}: served ${result.servedDay} for requested ${expected}, outside this reader's allowed date range. The response is not shown.`;
    } else {
      answeredDate = expected;
      servedDate = "servedDay" in result ? result.servedDay : result.requestedDay;
      if (servedDate !== expected && context.policy !== "static") {
        temporalNotice = result.state === "ready"
          ? `${request.subject}: requested ${expected}; showing data served for ${servedDate}.`
          : `${request.subject}: requested ${expected}; the ${result.state === "absent" ? "governed absence" : "unpublished partition"} is recorded for ${servedDate}.`;
      }
    }
  }

  const refusal = temporalRefused
    ? { state: "upstream_unavailable" as const, fault: { kind: "contract" as const, message: temporalNotice! } }
    : undefined;
  const data: Q["data"] | typeof refusal = refusal ?? result;
  return { ...query, data, temporalNotice, temporalRefused, servedDate, answeredDate };
}

interface FieldQuery {
  data: { requestedDay: string; observedDay: string | null; availability: string } | undefined;
  isPlaceholderData?: boolean;
  isSuccess?: boolean;
}

/** Daily field collections use observedDay as their served day and preserve typed unavailable results. */
export function withParquetFieldDayContract<Q extends FieldQuery>(
  query: Q,
  requestedDay: string | undefined,
  subject: string,
  retainedDay?: string
) {
  const field = query.data;
  const expected = query.isPlaceholderData ? retainedDay : requestedDay ?? field?.requestedDay;
  const temporalRefused = field !== undefined && (
    expected === undefined || !isWithin(field.requestedDay, expected, 0) ||
    (field.availability === "published" && field.observedDay !== field.requestedDay)
  );
  const temporalNotice = temporalRefused
    ? query.isPlaceholderData && retainedDay === undefined
      ? `${subject}: the retained response has not been verified for its original requested day. The response is not shown.`
      : `${subject}: requested ${expected ?? "an unknown day"}, response requested ${field.requestedDay} and observed ${field.observedDay ?? "no day"}. The response is not shown.`
    : null;
  const data: Q["data"] = temporalRefused ? undefined : field;
  return { ...query, data, temporalNotice, temporalRefused,
    isSuccess: query.isSuccess === true && !temporalRefused,
    answeredDate: temporalRefused ? undefined : field?.requestedDay };
}
