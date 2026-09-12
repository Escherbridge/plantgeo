/**
 * UTC day resolution shared by Parquet readers.
 *
 * This module deliberately has no database imports. Serving code can resolve the selected day
 * without loading the retired PostgreSQL environmental read model.
 */

const CALENDAR_DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

/** How a reader answers the optional day supplied by the map or agent. */
export type RequestedObservationDay =
  | { kind: "live" }
  | { kind: "historical"; date: string }
  | { kind: "unobserved"; date: string; reason: string };

/** Server UTC today; the only definition of the live edge used by Parquet serving. */
export function serverCurrentDate(nowMs: number = Date.now()): string {
  return new Date(nowMs).toISOString().slice(0, 10);
}

/** Resolve an optional selected day without substituting another observed day. */
export function resolveRequestedObservationDay(
  date: string | undefined,
  today: string = serverCurrentDate()
): RequestedObservationDay {
  if (date === undefined) return { kind: "live" };
  if (!CALENDAR_DATE_PATTERN.test(date) || Number.isNaN(Date.parse(`${date}T00:00:00Z`))) {
    return { kind: "unobserved", date, reason: `"${date}" is not a calendar date.` };
  }
  if (date === today) return { kind: "live" };
  if (date > today) {
    return {
      kind: "unobserved",
      date,
      reason: `Nothing is observed on ${date}; the server's today is ${today}.`,
    };
  }
  return { kind: "historical", date };
}

