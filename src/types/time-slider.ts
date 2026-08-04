/**
 * Shared time-slider contract. The client and (later) the server both import these
 * types so "no data" is described with one vocabulary -- see the availability/reason
 * discipline of PublishedDroughtCollection in
 * src/lib/server/services/environmental-read-model.ts.
 */

/** Which series a value came from. Observations carry no distribution band. */
export type MetricVariant = "observed" | "monte_carlo" | "ml";

/** The two variants a user can pick for a future date. */
export type ForecastVariant = "monte_carlo" | "ml";

/** How a layer behaves over time; decides whether a future date is even meaningful. */
export type TemporalKind = "snapshot" | "daily_series" | "event";

/**
 * One layer's value for one day at one enduring place, as the map consumes it.
 * There is no metric fact table: the server reads geo.features joined to geo.geometry (and
 * geo.drought_areas) directly -- see the read-model header in
 * src/lib/server/services/environmental-read-model.ts.
 */
export interface MetricAtDateFeature {
  geometryId: string;
  geometry: GeoJSON.Geometry;
  medianValue: number;
  /** p10 on ML; null on observations. */
  lowValue: number | null;
  /** p90 on ML; null on observations. */
  highValue: number | null;
  valueKind: "observed" | "forecast";
  variant: MetricVariant;
  /** YYYY-MM-DD */
  issuedOn: string;
  provenanceKey: string;
}

/** MetricAtDateFeature as it rides in a GeoJSON feature's `properties`. */
export type MetricAtDateProperties = Omit<MetricAtDateFeature, "geometry">;

/** Why a layer has nothing to draw. Never collapse these into an empty collection. */
export type MetricAtDateAvailability =
  | "published"
  /** Date precedes this layer's earliest version. */
  | "not_yet_observed"
  /** temporal_kind='event' and the date is in the future. */
  | "not_forecastable"
  /** date > serverCurrentDate + forecastHorizonDays. */
  | "beyond_horizon"
  /** Requested variant absent from forecastVariants. */
  | "variant_unavailable"
  | "not_published"
  /**
   * The request itself did not complete: a 500, a dropped connection, a database fault.
   * CLIENT-ONLY -- the server never returns it, because a server that can answer at all can
   * say which of the members above applies. It exists so `query.isError` stops being folded
   * into `not_published`, which turned a transport failure into a positive claim that the
   * warehouse published nothing that day; every consumer switching on availability then
   * rendered a blip as fact.
   */
  | "request_failed";

/** A GeoJSON collection that states why it is empty instead of just being empty. */
export interface MetricAtDateCollection extends GeoJSON.FeatureCollection {
  availability: MetricAtDateAvailability;
  reason: string | null;
}

/** What one geo.layers row supports temporally, as delivered to the browser. */
export interface SliderLayerCapability {
  /** geo.layers.name */
  layerName: string;
  temporalKind: TemporalKind;
  /** 0 = not forecastable. */
  forecastHorizonDays: number;
  forecastVariants: ForecastVariant[];
  /**
   * The first day the axis may start on; null when the layer has no observation yet.
   *
   * NOT `min(observed_day)`. Against the real warehouse a bare min() answers 1990-10-01 for
   * water-gauges -- discontinued gauges carry the timestamp of their final-ever reading --
   * and the slider renders a 36-year axis that is empty but for a few days at the right
   * edge. The server derives this from a continuity rule plus a density floor and reports
   * which one applied; see `EarliestObservedDateRule` in
   * src/lib/server/services/environmental-read-model.ts.
   */
  earliestObservedDate: string | null;
}

/** The server's answer to "what can the slider offer, and what day is it?". */
export interface SliderCapabilities {
  /** Server UTC today; the ONLY definition of "today". Never read the browser clock. */
  serverCurrentDate: string;
  layers: SliderLayerCapability[];
}

/** Input for `environmental.getMetricAtDate({ metric, date, variant, bbox })`. */
export interface MetricAtDateInput {
  /** Metric key; the server maps it to a backing layer and payload field. */
  metric: string;
  /** YYYY-MM-DD */
  date: string;
  variant: MetricVariant;
  /** "west,south,east,north"; omitted for an unbounded query. */
  bbox?: string;
}
