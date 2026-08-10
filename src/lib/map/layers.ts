import type {
  LayerSpecification,
  FillExtrusionLayerSpecification,
  DataDrivenPropertyValueSpecification,
} from "@maplibre/maplibre-gl-style-spec";
import { styleBackedLayerEntries } from "@/lib/map/layer-registry";

/**
 * One arm of a `match` paint expression: the feature value it matches, the colour it
 * paints and the caption the legend gives that colour. Exported alongside each class table
 * below so `src/lib/map/layer-legends.ts` legends the colours the map actually draws,
 * rather than a second hand-kept copy of them.
 */
export interface StyleClass {
  /** The property value this arm matches. */
  value: string;
  color: string;
  label: string;
}

/** One stop of an `interpolate` paint ramp, with the caption for its end of the bar. */
export interface StyleRampStop {
  value: number;
  color: string;
  label: string;
}

/**
 * The neutral grey every classified fill falls back to. Shared rather than repeated: it
 * means "the producer reported no class", and each layer captions that in its own words.
 */
export const UNCLASSIFIED_FILL_COLOR = "#9ca3af";

/** A `match` on one property, built from a class table so the legend reads what is painted. */
function matchClasses(
  property: string,
  classes: readonly StyleClass[],
  fallbackColor: string
): DataDrivenPropertyValueSpecification<string> {
  return [
    "match",
    ["get", property],
    ...classes.flatMap((styleClass) => [styleClass.value, styleClass.color]),
    fallbackColor,
  ] as unknown as DataDrivenPropertyValueSpecification<string>;
}

/** A linear `interpolate` over one numeric property, built from a stop table. */
function interpolateStops(
  property: string,
  stops: readonly StyleRampStop[]
): DataDrivenPropertyValueSpecification<string> {
  return [
    "interpolate",
    ["linear"],
    ["get", property],
    ...stops.flatMap((stop) => [stop.value, stop.color]),
  ] as unknown as DataDrivenPropertyValueSpecification<string>;
}

const MARTIN_SOURCE = "martin-dynamic";
// Table-backed OSM tiles live in a separate composite: mixing them with the
// function sources makes Martin declare vector_layers, which MapLibre then
// validates against and rejects every function-backed layer. See sources.ts.
const OSM_SOURCE = "martin-osm";

// GeoJSON source for the tRPC-fed layer at the bottom of this file. It is not declared in
// styles.ts, because a geojson source has no static URL to declare: the component owning
// the viewport query adds the source and sets its data, exactly as DroughtLayer and
// WaterLayer already do for "drought-monitor" and "water-gauges".
export const SOIL_SURVEY_SOURCE = "soil-survey";

export function buildings3dLayer(
  baseColor: string,
  accentColor: string
): FillExtrusionLayerSpecification {
  return {
    id: "buildings-3d",
    type: "fill-extrusion",
    source: "protomaps",
    "source-layer": "buildings",
    minzoom: 14,
    paint: {
      "fill-extrusion-height": [
        "coalesce",
        ["get", "render_height"],
        ["get", "height"],
        10,
      ],
      "fill-extrusion-base": [
        "coalesce",
        ["get", "render_min_height"],
        ["get", "min_height"],
        0,
      ],
      "fill-extrusion-color": [
        "interpolate",
        ["linear"],
        ["coalesce", ["get", "render_height"], ["get", "height"], 10],
        0,
        baseColor,
        50,
        accentColor,
        150,
        accentColor,
      ],
      "fill-extrusion-opacity": [
        "interpolate",
        ["linear"],
        ["zoom"],
        14,
        0,
        16,
        0.85,
      ],
    },
  };
}

// Layers below are hidden by default and revealed via their activeLayers
// toggles (see STYLE_LAYER_TOGGLE_MAP, synced in LayerManager).
// "severity" is derived from the WFIGS percentContained field at ingestion (wfigs.py
// perimeter_severity, whose cut points these captions restate: <25 / <50 / <75 / the rest).
// Perimeters whose containment is not reported render neutral grey rather than borrowing a
// severity colour, which is what the fallback caption below names.
export const FIRE_PERIMETER_SEVERITY_CLASSES: readonly StyleClass[] = [
  { value: "critical", color: "#dc2626", label: "Critical — under 25% contained" },
  { value: "high", color: "#ea580c", label: "High — 25 to 50%" },
  { value: "moderate", color: "#f59e0b", label: "Moderate — 50 to 75%" },
  { value: "low", color: "#fbbf24", label: "Low — 75% or more" },
];

export const FIRE_PERIMETER_UNCLASSIFIED_LABEL = "Containment not reported";

export const FIRE_PERIMETER_OUTLINE_COLOR = "#dc2626";

export const firePerimetersLayer: LayerSpecification = {
  id: "fire-perimeters",
  type: "fill",
  source: MARTIN_SOURCE,
  "source-layer": "fire_risk",
  minzoom: 4,
  layout: { visibility: "none" },
  paint: {
    "fill-color": matchClasses(
      "severity",
      FIRE_PERIMETER_SEVERITY_CLASSES,
      UNCLASSIFIED_FILL_COLOR
    ),
    "fill-opacity": 0.5,
  },
};

export const firePerimetersOutlineLayer: LayerSpecification = {
  id: "fire-perimeters-outline",
  type: "line",
  source: MARTIN_SOURCE,
  "source-layer": "fire_risk",
  minzoom: 4,
  layout: { visibility: "none" },
  paint: {
    "line-color": FIRE_PERIMETER_OUTLINE_COLOR,
    "line-width": 2,
  },
};

// "network" is the only sensor property the producer guarantees on every row:
// sensors.py._matches_networks rejects a station before collection unless its
// network matches the configured roster (ASOS/ASOS-HFM/RAWS/NonFedAWOS), so it
// is never optional the way station_name is. The fix to geo.sensor_tiles()'s
// SELECT list is written (drizzle/0010_sensor_tile_properties.sql), but not yet
// applied to production -- prod is migrated only through 0008 -- so until that
// migration runs, the live function still emits sensor_type/status/name, none
// of which any producer populates, and this layer keeps rendering every
// station in the neutral grey fallback. See src/components/map/AGENTS.md.
export const SENSOR_NETWORK_CLASSES: readonly StyleClass[] = [
  { value: "ASOS", color: "#0ea5e9", label: "ASOS" },
  { value: "ASOS-HFM", color: "#0284c7", label: "ASOS-HFM" },
  { value: "RAWS", color: "#f59e0b", label: "RAWS" },
  { value: "NonFedAWOS", color: "#22c55e", label: "NonFedAWOS" },
];

/** Every station reads this until 0010 reaches production -- see the note above. */
export const SENSOR_UNCLASSIFIED_LABEL = "Network not reported";

export const sensorsLayer: LayerSpecification = {
  id: "sensors",
  type: "circle",
  source: MARTIN_SOURCE,
  "source-layer": "sensors",
  minzoom: 4,
  layout: { visibility: "none" },
  paint: {
    "circle-color": matchClasses("network", SENSOR_NETWORK_CLASSES, UNCLASSIFIED_FILL_COLOR),
    "circle-radius": ["interpolate", ["linear"], ["zoom"], 4, 2, 8, 4, 12, 6],
    "circle-stroke-width": 1,
    "circle-stroke-color": "#ffffff",
    "circle-opacity": 0.85,
  },
};

// Oregon OEM fire-evacuation areas. "severity" is deterministically derived from
// evacuationLevel at ingestion (evacuation_zones.py EVACUATION_LEVEL_SEVERITIES) and
// reuses fire-perimeters' vocabulary minus "low" -- Oregon's scale has three levels, not
// four. The captions run the mapping back the other way (3 -> critical, 2 -> high, 1 ->
// moderate) because the level number is what a reader sees on a road sign.
export const EVACUATION_SEVERITY_CLASSES: readonly StyleClass[] = [
  { value: "critical", color: "#dc2626", label: "Level 3 — Go now" },
  { value: "high", color: "#ea580c", label: "Level 2 — Be set" },
  { value: "moderate", color: "#f59e0b", label: "Level 1 — Be ready" },
];

export const EVACUATION_UNCLASSIFIED_LABEL = "Level not reported";

export const EVACUATION_OUTLINE_COLOR = "#dc2626";

export const evacuationZonesLayer: LayerSpecification = {
  id: "evacuation-zones",
  type: "fill",
  source: MARTIN_SOURCE,
  "source-layer": "evacuation_zones",
  minzoom: 4,
  layout: { visibility: "none" },
  paint: {
    "fill-color": matchClasses(
      "severity",
      EVACUATION_SEVERITY_CLASSES,
      UNCLASSIFIED_FILL_COLOR
    ),
    "fill-opacity": 0.45,
  },
};

export const evacuationZonesOutlineLayer: LayerSpecification = {
  id: "evacuation-zones-outline",
  type: "line",
  source: MARTIN_SOURCE,
  "source-layer": "evacuation_zones",
  minzoom: 4,
  layout: { visibility: "none" },
  paint: {
    "line-color": EVACUATION_OUTLINE_COLOR,
    "line-width": 1.5,
  },
};

// MTBS burned-area boundaries, served by geo.burn_severity_tiles()
// (drizzle/0012_burn_severity_tiles.sql). Painted on "acres" and deliberately NOT on
// "severity_class": that column is null on all 478 published rows, because MTBS
// distributes burn severity as a thematic raster and publishes no polygon-level class
// (see mtbs.py MtbsBurnSeverityRecord.severity_class -- null there means "the source
// classifies nothing", never "unburned"). Keying the fill to it would repaint every scar
// in the neutral fallback, the same fabricated-field bug interventions and sensors each
// had. The per-fire dNBR numbers the layer does carry are mapping *calibration*, not an
// outcome, so they cannot stand in for it either.
//
// Burned area is the ordinal magnitude this layer genuinely measures, and it spans two
// and a half orders of magnitude (993 to 413,425 acres in production, median 3,727), so
// the stops are log-spaced rather than even -- even stops would put ~75% of scars in the
// first bucket. Ramp runs amber to deep maroon, reading as scar depth, and shares its
// palette with fire-perimeters above without reusing that layer's containment vocabulary.
// Scars with no reported acreage stay neutral grey rather than borrowing the low end.
export const BURN_SEVERITY_ACRES_STOPS: readonly StyleRampStop[] = [
  { value: 1000, color: "#fbbf24", label: "1k acres" },
  { value: 5000, color: "#f59e0b", label: "5k" },
  { value: 25000, color: "#ea580c", label: "25k" },
  { value: 100000, color: "#b91c1c", label: "100k" },
  { value: 400000, color: "#7f1d1d", label: "400k acres" },
];

export const BURN_SEVERITY_UNCLASSIFIED_LABEL = "Acreage not reported";

export const BURN_SEVERITY_OUTLINE_COLOR = "#7f1d1d";

export const burnSeverityLayer: LayerSpecification = {
  id: "burn-severity",
  type: "fill",
  source: MARTIN_SOURCE,
  "source-layer": "burn_severity",
  minzoom: 4,
  layout: { visibility: "none" },
  paint: {
    "fill-color": [
      "case",
      ["has", "acres"],
      interpolateStops("acres", BURN_SEVERITY_ACRES_STOPS),
      UNCLASSIFIED_FILL_COLOR,
    ] as unknown as DataDrivenPropertyValueSpecification<string>,
    "fill-opacity": 0.4,
  },
};

// Thinner and darker than the evacuation-zone outline above: a burn scar is historical
// context drawn beneath live incident data and must not out-shout an active perimeter.
export const burnSeverityOutlineLayer: LayerSpecification = {
  id: "burn-severity-outline",
  type: "line",
  source: MARTIN_SOURCE,
  "source-layer": "burn_severity",
  minzoom: 4,
  layout: { visibility: "none" },
  paint: {
    "line-color": BURN_SEVERITY_OUTLINE_COLOR,
    "line-width": 1,
    "line-opacity": 0.8,
  },
};

// Styled on "priority" because that is the only classification createIntervention
// actually persists; "intervention_type" is never written, so colouring by it painted
// every zone the same fabricated hue.
export const INTERVENTION_PRIORITY_CLASSES: readonly StyleClass[] = [
  { value: "High", color: "#b45309", label: "High priority" },
  { value: "Medium", color: "#6d28d9", label: "Medium priority" },
  { value: "Low", color: "#0369a1", label: "Low priority" },
];

export const INTERVENTION_UNCLASSIFIED_LABEL = "Priority not set";

export const INTERVENTION_OUTLINE_COLOR = "#4b5563";

/**
 * The colour a submitted site takes when it carries no priority, and deliberately NOT the
 * neutral `UNCLASSIFIED_FILL_COLOR` the classified fills fall back to.
 *
 * `interventions.submitIntervention` persists name, type, description, geometry, submitter
 * and consent -- it never writes `priority` -- so "no priority" is what essentially every
 * interactively submitted site carries, not an anomaly. Grey would report the common case
 * as a missing value; this teal reads as what it is, an approved site nobody has triaged.
 */
export const INTERVENTION_UNPRIORITIZED_POINT_COLOR = "#0f766e";

export const INTERVENTION_UNPRIORITIZED_POINT_LABEL = "Submitted site, not yet prioritised";

export const interventionsLayer: LayerSpecification = {
  id: "interventions",
  type: "fill",
  source: MARTIN_SOURCE,
  "source-layer": "interventions",
  minzoom: 6,
  layout: { visibility: "none" },
  paint: {
    "fill-color": matchClasses(
      "priority",
      INTERVENTION_PRIORITY_CLASSES,
      UNCLASSIFIED_FILL_COLOR
    ),
    "fill-opacity": 0.4,
  },
};

export const interventionsOutlineLayer: LayerSpecification = {
  id: "interventions-outline",
  type: "line",
  source: MARTIN_SOURCE,
  "source-layer": "interventions",
  minzoom: 6,
  layout: { visibility: "none" },
  paint: {
    "line-color": INTERVENTION_OUTLINE_COLOR,
    "line-width": 1,
    "line-dasharray": [2, 1],
  },
};

/**
 * The interactive submission path draws here, and nowhere else.
 * `InterventionSubmitModal` submits `{ type: "Point" }` geometry -- point-only by design,
 * because no polygon drawing tool exists -- and a MapLibre fill or line layer ignores Point
 * features entirely, so before this layer existed an approved recommendation reached the
 * tile and then painted nothing at all.
 *
 * The geometry-type filter keeps the three intervention layers disjoint: an ingested
 * polygon stays with the fill and its dashed outline, a submitted point gets a circle, and
 * neither draws the other's geometry. minzoom matches the fill (6) so the whole toggle
 * appears and disappears at one zoom rather than half of it at a time.
 */
export const interventionsPointsLayer: LayerSpecification = {
  id: "interventions-points",
  type: "circle",
  source: MARTIN_SOURCE,
  "source-layer": "interventions",
  minzoom: 6,
  layout: { visibility: "none" },
  filter: ["==", ["geometry-type"], "Point"],
  paint: {
    "circle-color": matchClasses(
      "priority",
      INTERVENTION_PRIORITY_CLASSES,
      INTERVENTION_UNPRIORITIZED_POINT_COLOR
    ),
    "circle-radius": ["interpolate", ["linear"], ["zoom"], 6, 4, 10, 6, 14, 9],
    // A white ring, as sensors uses: it separates sites clustered on one parcel and keeps
    // the dot legible over both the light and the dark basemap.
    "circle-stroke-width": 1.5,
    "circle-stroke-color": "#ffffff",
    "circle-opacity": 0.9,
  },
};

// Martin-served 3D building footprints from geo.osm_buildings — a separate
// dataset from the always-on protomaps "buildings" basemap layer above.
// "building_tiles" is the Martin *source id*; the MVT layer name emitted by
// ST_AsMVT inside geo.building_tiles() is "buildings" (drizzle/0001:499).
export const buildingFootprintsLayer: FillExtrusionLayerSpecification = {
  id: "building-footprints",
  type: "fill-extrusion",
  source: MARTIN_SOURCE,
  "source-layer": "buildings",
  minzoom: 13,
  layout: { visibility: "none" },
  paint: {
    "fill-extrusion-height": ["coalesce", ["get", "height"], 8],
    "fill-extrusion-base": ["coalesce", ["get", "min_height"], 0],
    "fill-extrusion-color": "#8b5cf6",
    "fill-extrusion-opacity": 0.75,
  },
};

// USGS WBD HUC12 boundaries, served by geo.watershed_tiles()
// (drizzle/0017_watershed_persistence.sql), whose ST_AsMVT tag -- and so the
// `source-layer` below -- is exactly "watersheds"; a mismatch renders nothing and reports
// no error. Painted flat rather than data-driven: a basin boundary carries no measurement,
// only identity (huc12/name/areasqkm/tohuc/states/hutype), so there is no class or ramp to
// key a colour to the way "severity" keys evacuation zones.
//
// There is no minzoom any more, and its removal is the point of
// drizzle/0023_watershed_zoom_generalization.sql.
//
// The floor was a payload measurement, not an aesthetic one. 9,396 published PNW basins with
// dense HUC12 outlines, measured against the production Martin on 2026-08-08 (lon -122,
// lat 45.5): z4 x2 y5 was 2.54 MB, z5 x5 y11 3.80 MB — the default camera cost 7.6 MB a
// viewport, so z7 was the lowest zoom the client could reach. The cost was real; the remedy
// was not. A layer that vanishes below a zoom is indistinguishable from a layer with no data,
// and that is the confusion this platform keeps having to correct.
//
// The tile function now answers each zoom with the rung of the HUC hierarchy that zoom can
// legibly draw — HUC4 far out through HUC12 close in, each level the exact union of its
// members — so a low-zoom tile carries tens of polygons instead of thousands and the layer is
// visible everywhere. Payload is bounded by the generalization, not by hiding the layer.
export const WATERSHED_BOUNDARY_COLOR = "#1565c0";

export const watershedsLayer: LayerSpecification = {
  id: "watersheds-fill",
  type: "fill",
  source: MARTIN_SOURCE,
  "source-layer": "watersheds",
  layout: { visibility: "none" },
  paint: {
    "fill-color": WATERSHED_BOUNDARY_COLOR,
    // A deliberate wash, kept at 0.05: the fill exists to be hit-testable (the hover
    // tooltip queries "watersheds-fill") and to tint a basin, not to be seen as a colour.
    // The outline is what a reader is meant to read the boundary off.
    "fill-opacity": 0.05,
  },
};

export const watershedsOutlineLayer: LayerSpecification = {
  id: "watersheds-outline",
  type: "line",
  source: MARTIN_SOURCE,
  "source-layer": "watersheds",
  layout: { visibility: "none" },
  paint: {
    "line-color": WATERSHED_BOUNDARY_COLOR,
    // Strengthened from 1px/0.6 on 2026-08-08. With the fill at a 0.05 wash the outline is
    // the entire layer, and basins are small enough on screen that a 1px hairline at 0.6 read
    // as a smudge rather than as a boundary.
    "line-width": 1.4,
    "line-opacity": 0.8,
  },
};

export const roadsLayer: LayerSpecification = {
  id: "osm-roads",
  type: "line",
  source: OSM_SOURCE,
  "source-layer": "osm_roads",
  minzoom: 10,
  paint: {
    "line-color": "#94a3b8",
    "line-width": ["interpolate", ["linear"], ["zoom"], 10, 0.5, 16, 3],
  },
};

export const waterwaysLayer: LayerSpecification = {
  id: "osm-waterways",
  type: "line",
  source: OSM_SOURCE,
  "source-layer": "osm_waterways",
  minzoom: 8,
  paint: {
    "line-color": "#3b82f6",
    "line-width": ["interpolate", ["linear"], ["zoom"], 8, 0.5, 16, 3],
  },
};

// ---------------------------------------------------------------------------
// tRPC-fed GeoJSON layer
//
// This draws from a per-viewport tRPC query (environmental.getSoilSurvey) instead of a
// Martin tile source, so it is deliberately absent from getLayers(): the three static
// styles in styles.ts declare only vector/raster sources, and baking a geojson-backed
// layer into them would point at a source that does not exist until the query resolves.
// It also carries no `visibility: "none"`, because presence -- not a layout property --
// is how a component-added layer is toggled off; setSource/addLayer and removal are the
// whole lifecycle. Declared here so the paint lives in one place beside its siblings.
// ---------------------------------------------------------------------------

// USDA SSURGO map units. "drainageClass" is the one property usda-soil.ts sets on
// every feature it emits -- normalizeDrainageClass always returns a string -- which is
// why the fill keys to it rather than to mukey or hydric. normalizeDrainageClass tests
// its specific phrases ("very poorly", "somewhat poorly", "moderately well", "somewhat
// excessively") before the general ones ("poorly", "excessively", "well") that would
// otherwise swallow them, so all seven of its stable ids are reachable and each gets its
// own arm here, ordered driest to wettest: excessively- and somewhat-excessively-drained
// (warm/dry), well- and moderately-well-drained (green, the two "good" classes), then
// somewhat-poorly-, poorly-, and very-poorly-drained (blue, deepening with wetness).
// Anything the function can't classify falls through as the raw SSURGO string or
// "unknown" and hits the neutral grey default -- a degraded but honest fallback rather
// than a wrong colour.
export const SOIL_SURVEY_DRAINAGE_CLASSES: readonly StyleClass[] = [
  { value: "excessively-drained", color: "#d97706", label: "Excessively drained" },
  {
    value: "somewhat-excessively-drained",
    color: "#ca8a04",
    label: "Somewhat excessively drained",
  },
  { value: "well-drained", color: "#65a30d", label: "Well drained" },
  { value: "moderately-well-drained", color: "#0d9488", label: "Moderately well drained" },
  { value: "somewhat-poorly-drained", color: "#0284c7", label: "Somewhat poorly drained" },
  { value: "poorly-drained", color: "#1d4ed8", label: "Poorly drained" },
  { value: "very-poorly-drained", color: "#1e3a8a", label: "Very poorly drained" },
];

export const SOIL_SURVEY_UNCLASSIFIED_LABEL = "Drainage not classified";

export const SOIL_SURVEY_OUTLINE_COLOR = "#78716c";

export const soilSurveyLayer: LayerSpecification = {
  id: "soil-survey-fill",
  type: "fill",
  source: SOIL_SURVEY_SOURCE,
  paint: {
    "fill-color": matchClasses(
      "drainageClass",
      SOIL_SURVEY_DRAINAGE_CLASSES,
      UNCLASSIFIED_FILL_COLOR
    ),
    "fill-opacity": 0.35,
  },
};

export const soilSurveyOutlineLayer: LayerSpecification = {
  id: "soil-survey-outline",
  type: "line",
  source: SOIL_SURVEY_SOURCE,
  paint: {
    "line-color": SOIL_SURVEY_OUTLINE_COLOR,
    "line-width": 0.5,
    "line-opacity": 0.7,
  },
};

/**
 * Graduated radii for the zoomed-out soil-survey lattice: how many real SSURGO delineations
 * one cell counted, and how big its dot is drawn. Roughly log-spaced because the counts are
 * — a 1/8-degree cell holds ~650 and a lattice cell can hold tens of thousands — and a
 * linear scale would collapse every ordinary cell into the same dot.
 *
 * No legend ramp reads this: size is not a swatch, so the soil-survey legend describes it
 * in a `note` block instead. The table is here to keep the expression readable.
 */
const SOIL_SURVEY_SUMMARY_COUNT_STOPS = [
  { count: 1, radius: 4 },
  { count: 250, radius: 8 },
  { count: 2_000, radius: 13 },
  { count: 20_000, radius: 19 },
] as const;

/**
 * One counted lattice cell per dot, drawn where the survey is too wide to outline —
 * `readSummaryFeatures` in usda-soil.ts. Colour is the cell's dominant drainage class, read
 * off the SAME table the detail fill matches on, so zooming in changes the shape a reader
 * sees without ever changing what a colour means. Size is the delineation count.
 *
 * The filter is what lets one GeoJSON source carry both shapes: a summary point and a
 * unioned polygon can never be painted by each other's layer. (The fill and line layers
 * above need no matching filter — MapLibre draws neither on a Point — but this one does,
 * or a polygon tier's features would each acquire a dot at their centroid.)
 */
export const soilSurveySummaryLayer: LayerSpecification = {
  id: "soil-survey-summary",
  type: "circle",
  source: SOIL_SURVEY_SOURCE,
  filter: ["==", ["get", "summary"], true],
  paint: {
    "circle-color": matchClasses(
      "drainageClass",
      SOIL_SURVEY_DRAINAGE_CLASSES,
      UNCLASSIFIED_FILL_COLOR
    ),
    "circle-radius": [
      "interpolate",
      ["linear"],
      ["get", "mapUnitCount"],
      ...SOIL_SURVEY_SUMMARY_COUNT_STOPS.flatMap((stop) => [stop.count, stop.radius]),
    ] as unknown as DataDrivenPropertyValueSpecification<number>,
    "circle-opacity": 0.8,
    "circle-stroke-width": 1,
    "circle-stroke-color": SOIL_SURVEY_OUTLINE_COLOR,
    "circle-stroke-opacity": 0.9,
  },
};

export function getLayers(): LayerSpecification[] {
  return [
    firePerimetersLayer,
    firePerimetersOutlineLayer,
    sensorsLayer,
    evacuationZonesLayer,
    evacuationZonesOutlineLayer,
    burnSeverityLayer,
    burnSeverityOutlineLayer,
    interventionsLayer,
    interventionsOutlineLayer,
    // After the outline so submitted sites draw over any zone they sit inside.
    interventionsPointsLayer,
    buildingFootprintsLayer,
    watershedsLayer,
    watershedsOutlineLayer,
    roadsLayer,
    waterwaysLayer,
  ];
}

/**
 * Maps an activeLayers toggle id to the concrete style layer ids it controls.
 * Derived from the layer registry so a new style-backed toggle is one registry entry
 * rather than a hand-edit here that can silently disagree with the rest.
 */
export const STYLE_LAYER_TOGGLE_MAP: Record<string, string[]> = Object.fromEntries(
  styleBackedLayerEntries().map((entry) => [entry.toggleId, entry.styleLayerIds])
);
