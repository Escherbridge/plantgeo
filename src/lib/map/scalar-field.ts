/** Exact support geometry for value fields; see AGENTS.md §scalar-field. */
export interface ScalarFieldCell {
  west: number;
  south: number;
  east: number;
  north: number;
  value: number;
}

export interface ScalarFieldMesh {
  vertices: Float32Array;
  cells: readonly ScalarFieldCell[];
}

export function scalarFieldEnabled(layer: string, configured: string | undefined): boolean {
  return configured?.split(",").some((name) => name.trim() === layer) ?? false;
}

function mercatorY(latitude: number): number {
  return (1 - Math.log(Math.tan(Math.PI / 4 + latitude * Math.PI / 360)) / Math.PI) / 2;
}

/** A rejected collection remains entirely native; no support is silently dropped. */
export function buildScalarFieldMesh(
  collection: GeoJSON.FeatureCollection | null,
  valueProperty: string,
  range: readonly [number, number],
): ScalarFieldMesh | null {
  if (!collection?.features.length || collection.features.length > 20_000) return null;
  const cells: ScalarFieldCell[] = [];
  const seen = new Map<string, number>();
  let compatibility: string | undefined;
  for (const feature of collection.features) {
    const p = feature.properties;
    const value: unknown = p?.[valueProperty];
    if (typeof value !== "number" || !Number.isFinite(value) || value < range[0] || value > range[1]) return null;
    if (!feature.geometry || feature.geometry.type !== "Polygon" || feature.geometry.coordinates.length !== 1) return null;
    const ring = feature.geometry.coordinates[0];
    if (ring.length !== 5 || ring.some((point) => point.length !== 2 || point.some((n) => !Number.isFinite(n)))) return null;
    const west = Math.min(...ring.map((point) => point[0]));
    const east = Math.max(...ring.map((point) => point[0]));
    const south = Math.min(...ring.map((point) => point[1]));
    const north = Math.max(...ring.map((point) => point[1]));
    if (west < -180 || east > 180 || east - west >= 180 || south <= -85.051129 || north >= 85.051129 || west === east || south === north) return null;
    if (ring[0][0] !== ring[4][0] || ring[0][1] !== ring[4][1]) return null;
    const corners = new Set(ring.slice(0, 4).map(([x, y]) => `${x}:${y}`));
    if (corners.size !== 4 || ring.some(([x, y]) => (x !== west && x !== east) || (y !== south && y !== north))) return null;
    if (ring.slice(0, 4).some(([x, y], i) => x !== ring[i + 1][0] && y !== ring[i + 1][1])) return null;
    const width: unknown = p?.cellWidthDegrees;
    const height: unknown = p?.cellHeightDegrees;
    if (typeof width !== "number" || typeof height !== "number" || !Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0 || Math.abs(width - (east - west)) > 1e-8 || Math.abs(height - (north - south)) > 1e-8) return null;
    const day: unknown = p?.observedDay;
    if (typeof day !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(day) || !Number.isFinite(Date.parse(day)) || new Date(day).toISOString().slice(0, 10) !== day) return null;
    if (p?.supportKind !== "tessellated_cell" || !p.gridName || !p.metricUnit || !p.supportId) return null;
    const signature = JSON.stringify([p.gridName, p.metricUnit, p.observedDay, width, height]);
    if (compatibility !== undefined && signature !== compatibility) return null;
    compatibility = signature;
    const key = `${west}:${south}:${east}:${north}`;
    if (seen.has(key)) {
      if (seen.get(key) !== value) return null;
      continue;
    }
    // A uniform origin prevents overlapping or shifted supports within the same grid.
    if (cells.length && (Math.abs((west - cells[0].west) / width - Math.round((west - cells[0].west) / width)) > 1e-6 || Math.abs((south - cells[0].south) / height - Math.round((south - cells[0].south) / height)) > 1e-6)) return null;
    seen.set(key, value);
    cells.push({ west, south, east, north, value });
  }
  const vertices = new Float32Array(cells.length * 18);
  let offset = 0;
  for (const cell of cells) {
    const left = (cell.west + 180) / 360;
    const right = (cell.east + 180) / 360;
    const top = mercatorY(cell.north);
    const bottom = mercatorY(cell.south);
    for (const [x, y] of [[left, top], [left, bottom], [right, top], [right, top], [left, bottom], [right, bottom]]) {
      vertices.set([x, y, cell.value], offset);
      offset += 3;
    }
  }
  return { vertices, cells };
}

/** Projected support edges choose the inspection handoff independently of zoom rungs. */
export function scalarFieldCellSpacing(
  cells: readonly ScalarFieldCell[],
  project: (point: [number, number]) => { x: number; y: number },
): number {
  if (!cells.length) return 0;
  let spacing = 0;
  for (const cell of cells) {
    const center: [number, number] = [(cell.west + cell.east) / 2, (cell.south + cell.north) / 2];
    const left = project([cell.west, center[1]]);
    const right = project([cell.east, center[1]]);
    const top = project([center[0], cell.north]);
    const bottom = project([center[0], cell.south]);
    spacing = Math.max(spacing, Math.min(Math.hypot(right.x - left.x, right.y - left.y), Math.hypot(top.x - bottom.x, top.y - bottom.y)));
  }
  return spacing;
}
