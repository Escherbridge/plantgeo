/** Canonical `geo.layers.name` values shared by writers, readers, and probes. */

/** Weather observation layer; writers and the readiness probe must agree. */
export const WEATHER_LAYER_ID = process.env.WEATHER_LAYER_ID ?? "weather-observations";
