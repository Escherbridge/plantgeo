const ISO_WITH_TIMEZONE = /(?:Z|[+-]\d{2}:\d{2})$/i;
const FIRMS_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;
const MAX_FUTURE_SKEW_MS = 5 * 60 * 1_000;

/** Normalizes an explicitly zoned ISO-8601 observation timestamp. */
export function parseZonedObservationTime(value: unknown): string | null {
  if (typeof value !== "string" || !ISO_WITH_TIMEZONE.test(value.trim())) {
    return null;
  }
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? new Date(timestamp).toISOString() : null;
}

/** Normalizes FIRMS acquisition date/time fields as UTC. */
export function parseFirmsObservationTime(
  properties: object
): string | null {
  const values = properties as Record<string, unknown>;
  const explicit = parseZonedObservationTime(values.observedAt);
  if (explicit) return explicit;

  if (typeof values.acqDate !== "string") return null;
  const dateMatch = FIRMS_DATE.exec(values.acqDate.trim());
  if (!dateMatch) return null;

  const rawTime =
    typeof values.acqTime === "string" ||
    typeof values.acqTime === "number"
      ? String(values.acqTime).trim().padStart(4, "0")
      : "";
  if (!/^\d{4}$/.test(rawTime)) return null;

  const year = Number(dateMatch[1]);
  const month = Number(dateMatch[2]);
  const day = Number(dateMatch[3]);
  const hour = Number(rawTime.slice(0, 2));
  const minute = Number(rawTime.slice(2));
  if (hour > 23 || minute > 59) return null;

  const timestamp = Date.UTC(year, month - 1, day, hour, minute);
  const parsed = new Date(timestamp);
  if (
    parsed.getUTCFullYear() !== year ||
    parsed.getUTCMonth() !== month - 1 ||
    parsed.getUTCDate() !== day
  ) {
    return null;
  }
  return parsed.toISOString();
}

/** Rejects stale observations and implausible future timestamps. */
export function isFreshObservation(
  observedAt: string,
  maxAgeMs: number,
  nowMs = Date.now()
): boolean {
  const timestamp = Date.parse(observedAt);
  return (
    Number.isFinite(timestamp) &&
    timestamp >= nowMs - maxAgeMs &&
    timestamp <= nowMs + MAX_FUTURE_SKEW_MS
  );
}
