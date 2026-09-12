"use client";

import {
  FORECAST_STATUS_LABELS, FORECAST_VARIABLES, forecastDays, forecastHours,
  formatForecastValue, matchesForecastSelection, utcInstantKey,
  type ForecastSelection, type ForecastVariable, type SelectedWeatherForecast,
} from "@/lib/environmental/weather-forecast";

interface WeatherDetailsProps {
  productId: string;
  selected: ForecastSelection;
  forecast: SelectedWeatherForecast | null;
  onSelectTime: (validAt: string) => void;
  validAt: string;
}

const CARD_VARIABLES: ForecastVariable[] = [
  "temperature_2m", "apparent_temperature", "weather_code", "precipitation_probability",
  "precipitation", "wind_speed_10m", "wind_gusts_10m", "wind_direction_10m", "relative_humidity_2m",
];

/** Present one selected location and one immutable forecast run. */
export function WeatherDetails({ productId, selected, forecast, onSelectTime, validAt }: WeatherDetailsProps) {
  if (!forecast || !matchesForecastSelection(forecast, selected, productId)) {
    return <p role="status">Forecast unavailable for the selected place, window and run.</p>;
  }
  if (forecast.status !== "available" && forecast.status !== "stale_run") {
    return <p role="status">{FORECAST_STATUS_LABELS[forecast.status]}</p>;
  }
  const { run } = forecast;
  if (!run) return <p role="status">Forecast run unavailable.</p>;
  const time = new Intl.DateTimeFormat("en", {
    timeZone: selected.timezone, month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short",
  });
  const hours = forecastHours(forecast.values);
  const selectedInstant = utcInstantKey(validAt);
  const current = selectedInstant === null ? undefined : hours.get(selectedInstant);
  const daily = forecastDays(forecast.values, selected.timezone).slice(0, 10);
  return <section aria-label="Weather forecast" className="space-y-4 p-3 text-sm">
    <div>
      <h3 className="font-semibold">Weather forecast</h3>
      <p>{selected.latitude.toFixed(4)}, {selected.longitude.toFixed(4)} · {selected.timezone}</p>
      <p>Sampled model estimates · {run.model}</p>
      <p>{forecast.sample_distance_m === null ? "Sample distance unavailable"
        : `Sample ${(forecast.sample_distance_m / 1000).toFixed(2)} km from selected place`}</p>
      <p role="status" aria-live="polite">{FORECAST_STATUS_LABELS[forecast.status]} · Valid {time.format(new Date(validAt))}</p>
    </div>
    <dl className="grid grid-cols-2 gap-2">
      {CARD_VARIABLES.filter((variable) => run.variables.includes(variable)).map((variable) => <div key={variable}>
        <dt className="text-xs opacity-70">{FORECAST_VARIABLES[variable].label}</dt>
        <dd>{formatForecastValue(current?.get(variable))}</dd>
      </div>)}
    </dl>
    {current?.get("precipitation")?.interval_start && <p>
      Precipitation interval: {time.format(new Date(current.get("precipitation")!.interval_start!))}
      {" to "}{time.format(new Date(current.get("precipitation")!.interval_end!))}
    </p>}
    <table className="w-full text-left text-xs">
      <caption className="py-2 text-left font-semibold">Hourly forecast · first 48 available hours</caption>
      <thead><tr><th scope="col">Valid time</th><th scope="col">Temperature</th><th scope="col">Precipitation</th></tr></thead>
      <tbody>{[...hours].slice(0, 48).map(([at, fields]) => <tr key={at}>
        <th scope="row"><button type="button" className="min-h-11 min-w-11 text-left underline focus-visible:outline-2"
          aria-pressed={at === selectedInstant} onClick={() => onSelectTime(at)}>{time.format(new Date(at))}</button></th>
        <td>{formatForecastValue(fields.get("temperature_2m"))}</td>
        <td>{formatForecastValue(fields.get("precipitation"))}</td>
      </tr>)}</tbody>
    </table>
    <table className="w-full text-left text-xs">
      <caption className="py-2 text-left font-semibold">Daily outlook · selected timezone</caption>
      <thead><tr><th scope="col">Day</th><th scope="col">Low / high</th><th scope="col">Precipitation total</th></tr></thead>
      <tbody>{daily.map((day) => <tr key={day.day}>
        <th scope="row" className="py-2">{day.day}{!day.complete && " (partial day)"}</th>
        <td>{day.low === null ? "Not supplied" : `${day.low.toFixed(1)} / ${day.high!.toFixed(1)} °C`}</td>
        <td>{day.precipitation === null ? "Incomplete intervals" : `${day.precipitation.toFixed(1)} mm`}</td>
      </tr>)}</tbody>
    </table>
    <details><summary className="min-h-11 cursor-pointer">Model run and source</summary>
      <dl className="space-y-1 break-words">
        <dt>Model initialized</dt><dd>{run.model_init_at}</dd>
        <dt>Provider issued</dt><dd>{run.provider_issued_at ?? "Not supplied by provider"}</dd>
        <dt>PlantGeo fetched</dt><dd>{run.fetched_at}</dd>
        <dt>PlantGeo admitted</dt><dd>{run.admitted_at}</dd>
        <dt>PlantGeo published</dt><dd>{run.published_at}</dd>
        <dt>Run</dt><dd>{run.run_id}</dd>
        <dt>Support</dt><dd>Sample coordinates; no area between samples is implied.</dd>
        <dt>Source resolution</dt><dd>{run.support.source_resolution_m === null ? "Not supplied" : `${run.support.source_resolution_m} m`}</dd>
        <dt>Source and licence</dt><dd>{run.provider} · {run.licence}</dd>
      </dl>
    </details>
  </section>;
}
