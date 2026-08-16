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

/** A closed day range, both ends inclusive, YYYY-MM-DD. */
export interface DayRange {
  from: string;
  to: string;
}

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

/**
 * The streams the slider describes that are NOT geo.layers rows, keyed by the toggle each
 * one backs.
 *
 * Lives in this pure-type module rather than in the server read model because
 * `src/lib/map/layer-registry.ts` names these in `warehouseLayerName`, and the registry is
 * plain TypeScript that panels, stores and node-run tests all import -- it must not reach
 * into `environmental-read-model.ts` (and its `db` import) to learn a string.
 *
 * These five toggles used to carry `warehouseLayerName: null`, which cost them their slider
 * ENTIRELY: no capability meant no axis, no control, and `resolveLayerDate` falling through
 * to the server's today, so five of roughly ten dated layers were permanently pinned to the
 * live edge even though every one of their readers accepts a historical day. They are backed
 * by `geo.drought_areas` and by the two governed model-plane views, not by `geo.features`,
 * which is the only reason they were ever absent from the capability payload.
 *
 * Every name here is deliberately distinct from every `geo.layers.name`: one capability list
 * is keyed by `layerName`, so a collision would publish two rows a lookup cannot tell apart.
 *
 * The NASA POWER lane's streams are NOT here, and their absence is deliberate rather than an
 * omission. It published one `climate-field` stream for all nine signals until 2026-08-10, and
 * that single axis was computed over every one of the lane's eleven `signal_name`s unioned --
 * so the soil-wetness pilot, measured on a fraction of the lattice, was handed the same
 * `latestObservedDate`, the same gap list and the same scrubbable days as the full-lattice air
 * temperature field. There are now nine streams, one per signal, and they are FORMED from the
 * signal id by `climateFieldStreamName` in src/lib/environmental/climate-field.ts rather than
 * listed: nine hand-typed constants is nine chances to publish a name no capability answers
 * to. They cannot collide with a `geo.layers.name` structurally -- no row is named
 * `climate-field-*` -- which is the same guarantee this record gives by inspection.
 */
export const SLIDER_STREAM_LAYER_NAMES = {
  drought: "drought-areas",
  soilMoisture: "soil-field-moisture",
  soilTemperature: "soil-field-temperature",
  soilVapourPressureDeficit: "soil-field-vpd",
} as const;

/** One of the non-geo.features stream names above. */
export type SliderStreamLayerName =
  (typeof SLIDER_STREAM_LAYER_NAMES)[keyof typeof SLIDER_STREAM_LAYER_NAMES];

/**
 * Toggles registered as a deliberate SNAPSHOT capability rather than a day axis: the
 * capability resolver publishes these by name with no observation window, in place of the
 * `geo.layers` row or `SLIDER_STREAM_LAYER_NAMES` entry an axis-bearing toggle would carry.
 *
 * The one member today is `strategyRecommendations`. `layer-registry.ts` has always pointed
 * `warehouseLayerName` at the literal `"strategy-recommendations"`, and that string names
 * neither a `geo.layers` row nor a `SLIDER_STREAM_LAYER_NAMES` entry -- so the §9 capability
 * LEFT JOIN (docs/layer-lane-standard.md) has always dropped it silently: the three
 * `geo.mv_strategy_recommendations_*` tiers still draw the map fine, but the row reports no
 * history and no slider mounts. Naming it here, the same way the axis-bearing streams are
 * named, is what lets the resolver register an honest snapshot/reference row for it instead
 * of leaving the drop as an omission nothing states -- see the pre-aggregation-layer design's
 * §9, "the live bug", landed 2026-08-15.
 *
 * Lives beside `SLIDER_STREAM_LAYER_NAMES` for the same reason that one does: the registry,
 * the resolver and any test asserting agreement between them must all read the same string
 * from one place rather than each hand-typing it.
 */
export const SNAPSHOT_SURFACE_LAYER_NAMES = {
  strategyRecommendations: "strategy-recommendations",
} as const;

/** One of the declared-snapshot names above. */
export type SnapshotSurfaceLayerName =
  (typeof SNAPSHOT_SURFACE_LAYER_NAMES)[keyof typeof SNAPSHOT_SURFACE_LAYER_NAMES];

/** What one published stream supports temporally, as delivered to the browser. */
export interface SliderLayerCapability {
  /**
   * The stream this capability describes: a `geo.layers.name`, or one of
   * `SLIDER_STREAM_LAYER_NAMES` for a stream that is not backed by `geo.features`.
   */
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
  /**
   * The newest day this layer has published, or null when it has none.
   *
   * This is the day each layer's own slider OPENS on, which is why it is not
   * `max(observed_day)`: it is the newest day at or above the SAME density floor that decided
   * `earliestObservedDate`. A partially-ingested live-edge day carrying three of a layer's
   * usual ten thousand readings is a published day, but opening the map on it draws a
   * near-empty country and reads as an outage. Such a day is still on the axis and still
   * scrubbable -- it is reported in `thinRanges` instead, so the slider marks it rather than
   * defaults to it. Never `serverCurrentDate`: today is a clock reading, not an observation.
   */
  latestObservedDate: string | null;
  /**
   * Ranges within the layer's own axis where nothing is published.
   *
   * Anchored at `earliestObservedDate` and running to `serverCurrentDate`, NOT at
   * `min(observed_day)` -- see the note on `earliestObservedDate` for why a bare min() puts
   * water-gauges at 1990-10-01 and would turn this list into 36 years of noise. The closing
   * range therefore reports a stalled ingest lane as the hole it is, rather than letting the
   * axis end silently at the last day that happened to land.
   */
  coverageGaps: DayRange[];
  /**
   * Ranges whose days ARE published but fall under the axis density floor.
   *
   * Disjoint from `coverageGaps` by construction: a thin range is a run of consecutive
   * calendar days that every one of them published. These are observations, not holes, and
   * the distinction is the point -- a slider that drew them as gaps would assert the
   * warehouse recorded nothing on a day it recorded something.
   */
  thinRanges: DayRange[];
  /**
   * The oldest day `coverageGaps` and `thinRanges` COMPLETELY describe; null when they
   * describe this layer's whole axis.
   *
   * A day before this is UNDESCRIBED, and that is a third state, not a synonym for either
   * list's silence: absence from `coverageGaps` there is not evidence anything was published,
   * and absence from `thinRanges` is not evidence a published day was dense. Ask
   * `isDayDescribed` in src/stores/time-slider-store.ts before turning either silence into a
   * claim.
   *
   * A DAY rather than the boolean that preceded it, because the question every consumer has
   * is "is THIS day described?" and a flag cannot answer it. The flag was computed, shipped
   * and read by nothing, so a gap dropped by the range cap drew as solid coverage on the
   * track and -- through `coverageOnDay` and the agent's prompt -- licensed the sentence
   * "This is an observed absence: you may say there was none here on <day>" for a day that was
   * never ingested. That is exactly what the `not_published_on_viewed_date` branch exists to
   * prevent, so the boundary is now a value a consumer must consult rather than a report it
   * may skip.
   *
   * Conservative by construction: it is the `from` day of the oldest SURVIVING range, so some
   * days just below it are in fact described. Understating what we know is the safe direction;
   * overstating it is the bug.
   */
  describedFromDay: string | null;
}

/** The server's answer to "what can the slider offer, and what day is it?". */
export interface SliderCapabilities {
  /** Server UTC today; the ONLY definition of "today". Never read the browser clock. */
  serverCurrentDate: string;
  /**
   * Days of axis drawn to the RIGHT of today, so the observed/future boundary is visible
   * rather than sitting invisibly at the axis's right edge.
   *
   * Deliberately NOT a forecast horizon and never to be read as one. `forecastHorizonDays`
   * is per layer and is 0 everywhere, because this warehouse publishes no forecast series;
   * scrubbing anywhere in this span therefore makes every layer report `not_forecastable`,
   * which is the honest answer. The span exists so that a user can SEE where the record
   * stops -- with the axis ending at today, "everything to the left is observed" was a fact
   * the UI stated only in words, and the two forecast-variant buttons above it led people to
   * read the whole track as a forecast.
   *
   * Server-published rather than a client literal for the same reason the axis and "today"
   * are: the client may not invent any part of the domain it draws.
   */
  futureAxisDays: number;
  layers: SliderLayerCapability[];
  /**
   * True when the stream capability scan did not answer, so `layers` is known to be SHORT
   * rather than complete.
   *
   * The thirteen non-`geo.features` streams are read by a whole-table pass that can time out
   * (measured as a Cloudflare 524 on 2026-08-15 against a memory-throttled warehouse). That
   * read is allowed to fail -- omission lies about nothing -- but the client cannot act on an
   * omission it cannot see: a missing capability and a stream with no history are the same
   * absence, and treating them alike silently unmounted every stream-backed slider with
   * nothing anywhere saying why. This flag is the difference, and `LayerRow` reads it exactly
   * the way it reads a null payload: axis unknown, so keep the control and let it say so.
   */
  streamsUnavailable: boolean;
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
