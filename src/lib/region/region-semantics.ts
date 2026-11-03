interface Envelope {
  west: number;
  south: number;
  east: number;
  north: number;
}

export const REGION_SLUG_PATTERN = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
export const MAX_ANALYSIS_LATTICE_CELLS = 100_000;

export function containsEnvelope(outer: Envelope, inner: Envelope): boolean {
  return outer.west <= inner.west && inner.east <= outer.east &&
    outer.south <= inner.south && inner.north <= outer.north;
}

export function isKnownTimeZone(value: string): boolean {
  if (!/^[A-Za-z][A-Za-z0-9_+-]*(?:\/[A-Za-z0-9_+-]+)*$/.test(value)) return false;
  try {
    const canonical = new Intl.DateTimeFormat("en", { timeZone: value }).resolvedOptions().timeZone;
    return canonical.toLowerCase() !== value.toLowerCase() || canonical === value;
  } catch {
    return false;
  }
}

/** Exact decimal divisibility, matching the service's Decimal-based lattice admission. */
export function isAdmissibleAnalysisLattice(envelope: Envelope, pitch: number): boolean {
  const values = [envelope.west, envelope.east, envelope.south, envelope.north, pitch, 0.0001];
  if (!values.every(Number.isFinite) || pitch <= 0) return false;
  const parts = values.map((value) => {
    const [mantissa, exponent = "0"] = String(value).split("e");
    const fractionLength = mantissa.split(".")[1]?.length ?? 0;
    return { coefficient: BigInt(mantissa.replace(".", "")), scale: fractionLength - Number(exponent) };
  });
  const scale = Math.max(...parts.map((part) => part.scale));
  const [west, east, south, north, step, precision] = parts.map((part) => part.coefficient * 10n ** BigInt(scale - part.scale));
  if (step <= 0n || east <= west || north <= south) return false;
  if ((east - west) % step !== 0n || (north - south) % step !== 0n) return false;
  if (step % precision !== 0n || (2n * west + step) % (2n * precision) !== 0n ||
      (2n * south + step) % (2n * precision) !== 0n) return false;
  return ((east - west) / step) * ((north - south) / step) <= BigInt(MAX_ANALYSIS_LATTICE_CELLS);
}
