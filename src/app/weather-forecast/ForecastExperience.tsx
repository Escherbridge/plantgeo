"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { decodeForecastResponse, forecastQuerySchema, type ForecastResponse } from "@/lib/weather-forecast/contract";

const inputClass = "min-h-11 w-full rounded-lg border border-slate-500 bg-slate-900 px-3 text-white focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-cyan-300";
const buttonClass = "min-h-11 rounded-lg border border-slate-500 px-4 py-2 text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-cyan-300 disabled:opacity-40";
const value = (number: number | null | undefined, unit = "") => number == null ? "Missing" : `${number.toFixed(1)}${unit ? ` ${unit}` : ""}`;
const validLabel = (time: string) => new Date(time).toLocaleString("en-GB", { timeZone: "UTC", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });

/** Isolated fixture forecast review; see AGENTS.md for admission and ownership. */
export default function ForecastExperience() {
  const [result, setResult] = useState<ForecastResponse | null>(null);
  const [notice, setNotice] = useState("Select an exact fixture sample and a forecast window to read its published Parquet run.");
  const [loading, setLoading] = useState(false);
  const [selectedHour, setSelectedHour] = useState(0);
  const requestVersion = useRef(0);
  const pending = useRef<AbortController | null>(null);

  useEffect(() => () => pending.current?.abort(), []);

  function clearSelection() {
    requestVersion.current += 1;
    pending.current?.abort();
    setResult(null);
    setLoading(false);
    setNotice("Selection changed. Load the selected run and window.");
  }

  async function loadForecast(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const start = `${form.get("start")}:00Z`;
    const end = new Date(Date.parse(start) + Number(form.get("hours")) * 3600_000);
    const parsed = forecastQuerySchema.safeParse({
      run_id: form.get("run_id"), lat: form.get("lat"), lon: form.get("lon"),
      start, end: Number.isNaN(end.valueOf()) ? "" : end.toISOString(),
    });
    clearSelection();
    if (!parsed.success) {
      setNotice("Choose valid coordinates, a run and a whole UTC hour, with a window of 1–48 hours.");
      return;
    }
    const version = requestVersion.current;
    const controller = new AbortController();
    pending.current = controller;
    setLoading(true);
    setNotice("Reading the selected run from governed Parquet…");
    try {
      const query = new URLSearchParams(Object.entries(parsed.data).map(([key, item]) => [key, String(item)]));
      const response = await fetch(`/api/weather-forecast?${query}`, { signal: controller.signal, cache: "no-store" });
      if (!response.ok) throw new Error("The local governed forecast service is unavailable. No previous values are displayed.");
      const data = decodeForecastResponse(await response.json(), parsed.data);
      if (version !== requestVersion.current || controller.signal.aborted) return;
      setResult(data);
      setSelectedHour(0);
      setNotice(data.reason ?? `${data.hourly.length} fixture forecast hours loaded for the selected coordinates. All times UTC.`);
    } catch (error) {
      if (version !== requestVersion.current || controller.signal.aborted) return;
      setNotice(error instanceof Error ? error.message : "Forecast read failed. No values were substituted.");
    } finally {
      if (version === requestVersion.current) setLoading(false);
    }
  }

  const hour = result?.hourly[selectedHour];
  const unit = (name: string) => result?.variables[name]?.unit ?? "";

  return (
    <main className="h-full overflow-y-auto bg-slate-950 text-slate-100" aria-labelledby="forecast-title">
      <div className="mx-auto max-w-6xl space-y-7 px-4 py-8 sm:px-8">
        <header className="space-y-3">
          <p className="text-sm font-semibold uppercase tracking-widest text-cyan-300">PlantGeo · Forecast laboratory</p>
          <h1 id="forecast-title" className="text-3xl font-semibold sm:text-4xl">Weather forecast</h1>
          <p className="max-w-3xl text-slate-300">Explore one pinned forecast run, hour by hour, for an explicitly selected place. Values come from the local governed Parquet fixture.</p>
          <p className="rounded-xl border border-amber-600 bg-amber-950/50 p-4 text-amber-100"><strong>Synthetic fixture — not a live forecast.</strong> Source admission is pending. This deterministic sample has no probability or confidence intervals. Its fixed window is September 12–15, 2026 UTC; it does not roll forward with today.</p>
        </header>

        <section aria-labelledby="selection-title" className="rounded-2xl border border-slate-700 bg-slate-900/60 p-5">
          <h2 id="selection-title" className="mb-4 text-xl font-semibold">Choose place &amp; run</h2>
          <form onSubmit={loadForecast} onChange={clearSelection} className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <label className="space-y-2 text-sm">Selected latitude<input className={inputClass} name="lat" type="number" min="-90" max="90" step="any" defaultValue="40" required /></label>
            <label className="space-y-2 text-sm">Selected longitude<input className={inputClass} name="lon" type="number" min="-180" max="180" step="any" defaultValue="-105" required /></label>
            <label className="space-y-2 text-sm">Pinned run<input className={inputClass} name="run_id" defaultValue="fixture-20260912T000000Z-v1" required /></label>
            <label className="space-y-2 text-sm">First valid hour (UTC)<input className={inputClass} name="start" type="datetime-local" step="3600" defaultValue="2026-09-13T00:00" required /></label>
            <label className="space-y-2 text-sm">Window length (hours)<input className={inputClass} name="hours" type="number" min="1" max="48" step="1" defaultValue="48" required /></label>
            <button className={`${buttonClass} self-end bg-cyan-300 font-semibold text-slate-950`} type="submit">{loading ? "Reload selected forecast" : "Load forecast"}</button>
          </form>
          <p className="mt-4 text-sm text-slate-300">Supported synthetic samples: latitude 40 or 40.1, longitude −105 or −104.9. Other coordinates return outside-domain. Samples do not represent a continuous field.</p>
        </section>

        <p role="status" aria-live="polite" className="rounded-lg border border-slate-600 p-4">{result && <strong className="mr-2">{result.status}</strong>}{notice}</p>

        {result?.run && <section aria-label="Source and run identity" className="grid gap-3 break-words text-sm text-slate-300 sm:grid-cols-2">
          <p><strong className="text-white">Selected place:</strong> {result.request.lat}, {result.request.lon} · UTC<br />Support: {result.support ? `${result.support.kind}, exact sample (${result.support.latitude}, ${result.support.longitude}), ${result.support.distance_km} km away` : "No supported sample"}</p>
          <p><strong className="text-white">Model:</strong> {result.run.provider} / {result.run.model}<br />Run: {result.run.run_id}</p>
          <p>Initialization: {result.run.initialization_time}<br />Issue: {result.run.issue_time ?? "Not supplied"}</p>
          <p>Published: {result.run.published_at}<br />Synthetic lifecycle clock · Licence: {result.run.licence}</p>
          <p>Requested window: {result.request.start} to {result.request.end} (end exclusive)<br />Resolution: {result.run.source_resolution}</p>
          <p>{result.uncertainty.message}<br />Precipitation probability, weather codes and gusts: not supplied by this fixture.</p>
        </section>}

        {hour && result && <>
          <section aria-labelledby="hour-title" className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <h2 id="hour-title" className="text-2xl font-semibold">Hourly forecast · UTC</h2>
              <div className="flex gap-2">
                <button type="button" className={buttonClass} disabled={selectedHour === 0} onClick={() => setSelectedHour((index) => index - 1)}>Previous hour</button>
                <button type="button" className={buttonClass} disabled={selectedHour === result.hourly.length - 1} onClick={() => setSelectedHour((index) => index + 1)}>Next hour</button>
              </div>
            </div>
            <label className="block text-sm">Selected valid hour: {validLabel(hour.valid_time)} UTC
              <input aria-label="Forecast valid hour" aria-valuetext={`${validLabel(hour.valid_time)} UTC`} className="min-h-11 w-full accent-cyan-300" type="range" min="0" max={result.hourly.length - 1} value={selectedHour} onChange={(event) => setSelectedHour(Number(event.target.value))} />
            </label>
            <p aria-live="polite" className="text-sm text-cyan-200">Valid {hour.valid_time} · lead {hour.lead_hours} hours from initialization</p>
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
              {[
                ["Temperature", value(hour.values.temperature_2m, unit("temperature_2m"))],
                ["Precipitation", value(hour.values.precipitation, unit("precipitation"))],
                ["Humidity", value(hour.values.relative_humidity_2m, unit("relative_humidity_2m"))],
                ["Cloud cover", value(hour.values.cloud_cover, unit("cloud_cover"))],
                ["Wind", `${value(hour.values.wind_speed_10m, unit("wind_speed_10m"))} · ${value(hour.values.wind_direction_10m, "° from north")}`],
              ].map(([label, display]) => <div key={label} className="rounded-xl border border-slate-600 bg-slate-900 p-4"><p className="text-sm text-slate-300">{label}</p><p className="mt-2 text-xl font-semibold">{display}</p></div>)}
            </div>
            <p className="text-sm text-slate-300">Precipitation accumulation: [{hour.interval_start}, {hour.interval_end}). Wind direction is meteorological, from true north; a missing direction may indicate calm or missing wind.</p>
            {Object.keys(hour.missingness).length > 0 && <p className="text-sm text-amber-200">Missingness: {Object.entries(hour.missingness).map(([name, reason]) => `${name}: ${reason}`).join("; ")}</p>}
          </section>

          <section aria-labelledby="daily-title" className="space-y-4">
            <h2 id="daily-title" className="text-2xl font-semibold">Daily outlook</h2>
            <p className="text-sm text-slate-300">Derived UTC summaries of this requested window. Partial days and missing input hours remain explicit; the fixture has no 7–10 day horizon.</p>
            <div className="grid gap-4 sm:grid-cols-3">
              {result.daily.map((day) => <article key={day.day} className="space-y-2 rounded-xl border border-slate-600 bg-slate-900 p-5">
                <h3 className="text-lg font-semibold">{day.day} UTC</h3>
                <p className="text-sm text-cyan-200">{day.complete ? "Complete day" : "Partial day"} · {day.hours}/{day.expected_hours} hours</p>
                <p>High {value(day.temperature_max, unit("temperature_2m"))} / Low {value(day.temperature_min, unit("temperature_2m"))}</p>
                <p>Rain total: {value(day.precipitation_sum, unit("precipitation"))}</p>
                <p>Humidity: {value(day.relative_humidity_mean, "%")} · Cloud: {value(day.cloud_cover_mean, "%")}</p>
                <p>Mean vector wind: {value(day.wind_speed, "m/s")} · {value(day.wind_direction, "° from north")}</p>
                {Object.keys(day.missingness).length > 0 && <p className="text-sm text-amber-200">Daily missingness: {Object.entries(day.missingness).map(([name, reason]) => `${name}: ${reason}`).join("; ")}</p>}
              </article>)}
            </div>
          </section>

          <section aria-labelledby="table-title" className="space-y-3">
            <h2 id="table-title" className="text-xl font-semibold">All forecast hours</h2>
            <div className="overflow-x-auto rounded-xl border border-slate-600" tabIndex={0} role="region" aria-label="Scrollable hourly forecast table">
              <table className="w-full whitespace-nowrap text-left text-sm">
                <caption className="p-3 text-left text-slate-300">Same pinned run and selected place; UTC valid hour. Precipitation covers the following hour. Missing values are never zero-filled.</caption>
                <thead className="bg-slate-800"><tr>{["Valid time (UTC)", `Temperature (${unit("temperature_2m")})`, "Precipitation (mm, next hour)", "Humidity (%)", "Cloud (%)", "Wind (m/s)", "From (°)", "Missingness"].map((name) => <th key={name} scope="col" className="p-3">{name}</th>)}</tr></thead>
                <tbody>{result.hourly.map((row, index) => <tr key={row.valid_time} className={index === selectedHour ? "bg-cyan-950" : "border-t border-slate-700"}>
                  <th scope="row" className="p-2"><button type="button" className={buttonClass} aria-pressed={index === selectedHour} onClick={() => setSelectedHour(index)}>{validLabel(row.valid_time)}</button></th>
                  {[row.values.temperature_2m, row.values.precipitation, row.values.relative_humidity_2m, row.values.cloud_cover, row.values.wind_speed_10m, row.values.wind_direction_10m].map((number, column) => <td key={column} className="p-3">{value(number)}</td>)}
                  <td className="p-3">{Object.entries(row.missingness).map(([name, reason]) => `${name}: ${reason}`).join("; ") || "None"}</td>
                </tr>)}</tbody>
              </table>
            </div>
          </section>
        </>}
        <footer className="border-t border-slate-700 pt-5 text-sm text-slate-400">Local fixture review only. Shared map layer, capability catalogue and agent dispatcher registration await ownership transfer and source admission.</footer>
      </div>
    </main>
  );
}
