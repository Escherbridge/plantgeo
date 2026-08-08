/** The minimal shape `isRenderableWeatherObservation` needs -- structural, not the full row. */
export interface RenderableWeatherSignal {
  windSpeed: number | null;
  windDirection: number | null;
  temperature: number | null;
}

/**
 * The single source of the "what may be drawn" rule for a weather observation: true when at
 * least one drawable signal was actually measured, the wind pair (speed AND direction -- an
 * arrow needs both) or a temperature. Humidity never gates: it is tooltip-only. Both the
 * server read model and the map's client-side filter call this one function so the rule can
 * never drift between what the warehouse serves and what the map paints.
 */
export function isRenderableWeatherObservation(
  observation: RenderableWeatherSignal
): boolean {
  return (
    (observation.windSpeed !== null && observation.windDirection !== null) ||
    observation.temperature !== null
  );
}
