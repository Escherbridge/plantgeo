// Timestamp resolution + display formatting shared by the map hover tooltip and
// the click popups. No React, no maplibre imports -- keeps this testable in isolation.
// See src/components/map/AGENTS.md for why event times are shown absolutely.

type Properties = Record<string, unknown>;

const ISO_WITH_TIMEZONE = /(?:Z|[+-]\d{2}:\d{2})$/i;
const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/;
const ALL_DIGITS = /^\d+$/;

/** Epoch values below this magnitude are seconds, not milliseconds (~1973 in ms). */
const EPOCH_SECONDS_CUTOFF = 1e11;

function epochToIso(epoch: number): string | null {
  const milliseconds = Math.abs(epoch) < EPOCH_SECONDS_CUTOFF ? epoch * 1000 : epoch;
  const date = new Date(milliseconds);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

/**
 * Coerces an ISO string, an epoch number, or a numeric string to an ISO timestamp.
 * Upstream feeds are inconsistent: WFIGS reports epoch milliseconds, USGS reports
 * ISO, and vector-tile properties stringify both.
 */
export function toIsoTimestamp(value: unknown): string | null {
  if (typeof value === "number") {
    return Number.isFinite(value) ? epochToIso(value) : null;
  }
  if (typeof value !== "string") return null;

  const trimmed = value.trim();
  if (!trimmed) return null;
  if (ALL_DIGITS.test(trimmed)) {
    const epoch = Number(trimmed);
    return Number.isFinite(epoch) ? epochToIso(epoch) : null;
  }

  const parsed = Date.parse(trimmed);
  return Number.isFinite(parsed) ? new Date(parsed).toISOString() : null;
}

/** Resolves a FIRMS-style observedAt, or acqDate+acqTime, to an ISO timestamp. */
export function resolveObservationIso(properties: Properties): string | null {
  const observedAt = properties.observedAt;
  if (typeof observedAt === "string" && ISO_WITH_TIMEZONE.test(observedAt.trim())) {
    const timestamp = Date.parse(observedAt);
    if (Number.isFinite(timestamp)) return observedAt;
  }

  const acqDate = properties.acqDate;
  if (typeof acqDate === "string" && DATE_ONLY.test(acqDate.trim())) {
    const acqTimeRaw = properties.acqTime;
    const acqTime =
      typeof acqTimeRaw === "string" || typeof acqTimeRaw === "number"
        ? String(acqTimeRaw).trim().padStart(4, "0")
        : "";
    if (/^\d{4}$/.test(acqTime)) {
      const iso = `${acqDate.trim()}T${acqTime.slice(0, 2)}:${acqTime.slice(2)}:00Z`;
      const timestamp = Date.parse(iso);
      if (Number.isFinite(timestamp)) return iso;
    }
  }

  return null;
}

const DATE_FORMAT: Intl.DateTimeFormatOptions = {
  year: "numeric",
  month: "short",
  day: "numeric",
};

const DATE_TIME_FORMAT: Intl.DateTimeFormatOptions = {
  ...DATE_FORMAT,
  hour: "numeric",
  minute: "2-digit",
};

function parseIso(isoValue: string | null | undefined): Date | null {
  if (!isoValue) return null;
  const milliseconds = Date.parse(isoValue);
  return Number.isFinite(milliseconds) ? new Date(milliseconds) : null;
}

/** "Aug 2, 2026" -- absolute calendar date, or null when unparseable. */
export function formatAbsoluteDate(isoValue: string | null | undefined): string | null {
  const date = parseIso(isoValue);
  return date === null ? null : date.toLocaleDateString(undefined, DATE_FORMAT);
}

/** "Aug 2, 2026, 1:10 PM" -- absolute date and time, or null when unparseable. */
export function formatAbsoluteDateTime(isoValue: string | null | undefined): string | null {
  const date = parseIso(isoValue);
  return date === null ? null : date.toLocaleString(undefined, DATE_TIME_FORMAT);
}

/** "3h ago" -- coarse relative age, or null when unparseable. */
export function formatRelativeTime(isoValue: string | null | undefined): string | null {
  const date = parseIso(isoValue);
  if (date === null) return null;
  const diffSeconds = Math.max(0, Math.floor((Date.now() - date.getTime()) / 1000));
  if (diffSeconds < 60) return "just now";
  if (diffSeconds < 3600) return `${Math.floor(diffSeconds / 60)}m ago`;
  if (diffSeconds < 86400) return `${Math.floor(diffSeconds / 3600)}h ago`;
  return `${Math.floor(diffSeconds / 86400)}d ago`;
}

/**
 * "Aug 2, 2026, 1:10 PM (3h ago)" -- the absolute time answers "when did this
 * happen", the relative suffix answers "how stale is it". Null when unparseable.
 */
export function formatTimestampWithRelative(isoValue: string | null | undefined): string | null {
  const absolute = formatAbsoluteDateTime(isoValue);
  if (absolute === null) return null;
  const relative = formatRelativeTime(isoValue);
  return relative === null ? absolute : `${absolute} (${relative})`;
}
