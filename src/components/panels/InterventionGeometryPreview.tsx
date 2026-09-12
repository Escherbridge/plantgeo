import {
  InterventionGeometrySchema,
  countInterventionGeometryPositions,
  MAX_INTERVENTION_GEOMETRY_POSITIONS,
} from "@/lib/geo/intervention-geometry";

/** Show the submitted coordinates independently of published map tiles. */
export function InterventionGeometryPreview({ geometry }: { geometry: unknown }) {
  const parsed = InterventionGeometrySchema.safeParse(geometry);
  if (!parsed.success || countInterventionGeometryPositions(parsed.data) > MAX_INTERVENTION_GEOMETRY_POSITIONS) {
    return <p className="text-xs text-amber-300">The submitted geometry cannot be previewed. Request a corrected boundary before publication.</p>;
  }
  const site = parsed.data;
  const polygons = site.type === "Point" ? [] : site.type === "Polygon" ? [site.coordinates] : site.coordinates;
  const points = site.type === "Point" ? [site.coordinates] : polygons.flat(2);
  const longitudes = points.map((point) => point[0]);
  const latitudes = points.map((point) => point[1]);
  const west = Math.min(...longitudes);
  const east = Math.max(...longitudes);
  const south = Math.min(...latitudes);
  const north = Math.max(...latitudes);
  const extent = Math.max(east - west, north - south, 0.00001);
  const project = (point: number[]) => [20 + (point[0] - (west + east) / 2) / extent * 160 + 80,
    100 - (point[1] - (south + north) / 2) / extent * 160];
  const label = `${site.type} submitted geometry preview`;
  return (
    <figure className="rounded border border-zinc-700 bg-zinc-950 p-3">
      <svg viewBox="0 0 200 200" role="img" aria-label={label} className="h-44 w-full">
        <title>{label}</title>
        {site.type === "Point" ? <circle cx="100" cy="100" r="5" fill="#34d399" /> :
          polygons.map((polygon, index) => <path key={index}
            d={polygon.map((ring) => `${ring.map((position, i) => `${i === 0 ? "M" : "L"}${project(position).join(" ")}`).join(" ")} Z`).join(" ")}
            fill="#10b98133" fillRule="evenodd" stroke="#34d399" strokeWidth="2" />)}
      </svg>
      <figcaption className="text-xs text-zinc-300">
        Submitted {site.type} · {points.length} coordinate positions · north is up.
        <span className="block text-zinc-400">This preview shows the submission awaiting review. The public map only shows published sites.</span>
      </figcaption>
      <details className="mt-2 text-xs text-zinc-300">
        <summary className="cursor-pointer">Inspect submitted GeoJSON</summary>
        <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap break-all">{JSON.stringify(site, null, 2)}</pre>
      </details>
    </figure>
  );
}
