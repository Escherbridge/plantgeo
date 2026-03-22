const R = 6371000; // Earth radius in meters

function toRad(deg: number): number {
  return (deg * Math.PI) / 180;
}

export function haversineDistance(a: [number, number], b: [number, number]): number {
  const dLat = toRad(b[1] - a[1]);
  const dLng = toRad(b[0] - a[0]);
  const sinDLat = Math.sin(dLat / 2);
  const sinDLng = Math.sin(dLng / 2);
  const h =
    sinDLat * sinDLat +
    Math.cos(toRad(a[1])) * Math.cos(toRad(b[1])) * sinDLng * sinDLng;
  return 2 * R * Math.asin(Math.sqrt(h));
}

export function polygonArea(coords: [number, number][]): number {
  if (coords.length < 3) return 0;
  let area = 0;
  const n = coords.length;
  for (let i = 0; i < n; i++) {
    const j = (i + 1) % n;
    const xi = toRad(coords[i][0]);
    const yi = toRad(coords[i][1]);
    const xj = toRad(coords[j][0]);
    const yj = toRad(coords[j][1]);
    area += (xj - xi) * (2 + Math.sin(yi) + Math.sin(yj));
  }
  return Math.abs((area * R * R) / 2);
}

export function bearing(a: [number, number], b: [number, number]): number {
  const dLng = toRad(b[0] - a[0]);
  const lat1 = toRad(a[1]);
  const lat2 = toRad(b[1]);
  const y = Math.sin(dLng) * Math.cos(lat2);
  const x =
    Math.cos(lat1) * Math.sin(lat2) -
    Math.sin(lat1) * Math.cos(lat2) * Math.cos(dLng);
  return ((Math.atan2(y, x) * 180) / Math.PI + 360) % 360;
}

export function formatDistance(meters: number, unit: "metric" | "imperial"): string {
  if (unit === "imperial") {
    const feet = meters * 3.28084;
    if (feet < 5280) return `${feet.toFixed(0)} ft`;
    return `${(feet / 5280).toFixed(2)} mi`;
  }
  if (meters < 1000) return `${meters.toFixed(0)} m`;
  return `${(meters / 1000).toFixed(2)} km`;
}

export function formatArea(sqm: number, unit: "metric" | "imperial"): string {
  if (unit === "imperial") {
    const sqft = sqm * 10.7639;
    const acres = sqm / 4046.86;
    if (acres >= 1) return `${acres.toFixed(2)} ac`;
    return `${sqft.toFixed(0)} ft²`;
  }
  const ha = sqm / 10000;
  if (ha >= 1) return `${ha.toFixed(2)} ha`;
  return `${sqm.toFixed(0)} m²`;
}

export function lineStringDistance(coords: [number, number][]): number {
  let total = 0;
  for (let i = 1; i < coords.length; i++) {
    total += haversineDistance(coords[i - 1], coords[i]);
  }
  return total;
}
