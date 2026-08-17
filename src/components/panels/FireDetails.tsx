"use client";

import { Flame, Wind, AlertTriangle, MapPin, Droplets } from "lucide-react";
import { useFireData } from "@/hooks/useFireData";
import { useDebouncedLayerDay } from "@/lib/map/layer-toggle-context";
import { haversineDistance } from "@/lib/map/measurement";
import { trpc } from "@/lib/trpc/client";

/** Beyond this, the nearest sample describes other weather than the one under the cursor. */
const NEAR_ENOUGH_TO_READ_AS_HERE_KM = 60;

interface StatCardProps {
  icon: React.ReactNode;
  label: string;
  value: string | number;
  sub?: string;
  highlight?: boolean;
}

function StatCard({ icon, label, value, sub, highlight }: StatCardProps) {
  return (
    <div
      className={`rounded-lg border p-3 flex flex-col gap-1 ${
        highlight
          ? "border-red-300 bg-red-50 dark:bg-red-950/20"
          : "border-[hsl(var(--border))] bg-[hsl(var(--card))]"
      }`}
    >
      <div className="flex items-center gap-2 text-[hsl(var(--muted-foreground))]">
        {icon}
        <span className="text-xs font-medium">{label}</span>
      </div>
      <span
        className={`text-2xl font-bold ${highlight ? "text-red-600" : "text-[hsl(var(--foreground))]"}`}
      >
        {value}
      </span>
      {sub && (
        <span className="text-xs text-[hsl(var(--muted-foreground))]">{sub}</span>
      )}
    </div>
  );
}

/** Units match the map's weather tooltip exactly -- see hover-fields.ts §formatWeatherObservation. */
function reading(value: number | null | undefined, decimals: number, unit: string): string {
  return typeof value === "number" && Number.isFinite(value)
    ? `${value.toFixed(decimals)}${unit}`
    : "N/A";
}

interface FireDetailsProps {
  /** The map centre, which is the point the weather cards describe. */
  center: { lat: number; lon: number };
}

/**
 * What the fire layers are showing, as the Fire section of the map dock.
 *
 * Mounted only while that section is expanded -- which is what `useFireData`'s enabled flag
 * now reads, in place of the `open` prop the sheet passed it. The four `<LayerToggle>` rows
 * this panel used to carry are gone: the section's own layer rows are the switches now, and
 * two controls over one `activeLayers` entry, one of them out of sight, is the drift the dock
 * was built to end.
 *
 * The three weather cards read the warehouse rather than a constant. They were literal "N/A"
 * strings under a banner that claimed unavailability without ever asking -- a claim that was
 * false every time the ingest job had run, which is every hour. `getWeatherForPoint` is the
 * same nearest-published-observation query `regional-context` uses, so this panel and the
 * map's weather layer can never disagree about what has been published.
 */
export function FireDetails({ center }: FireDetailsProps) {
  // The `fire` row's own settled day -- the same one LayerManager gives the map's fire layer,
  // so the count here can never describe a different date than the pins beside it: one query
  // key, one answer. Keyed by the toggle rather than by anything map-wide, because since
  // 2026-08-09 every layer scrubs its own axis and there is no map-wide day to borrow. The
  // count below is a count OF THOSE PINS, so it has to move with them and with nothing else.
  const { requestDate } = useDebouncedLayerDay("fire");
  const fireData = useFireData(true, requestDate);
  const effectiveFireCount = fireData.count;
  // `data`/`count` may still be a RETAINED previous day's answer -- loading is not the only
  // state that makes them untrustworthy, see useFireData's `isStaleForRequestedDate` doc.
  const fireCountUnsettled = fireData.isLoading || fireData.isStaleForRequestedDate;
  const fireCountSub =
    requestDate === undefined
      ? "Published FIRMS detections, live window"
      : `Published FIRMS detections, ${requestDate}`;

  const weatherQuery = trpc.wildfire.getWeatherForPoint.useQuery({
    lat: center.lat,
    lon: center.lon,
  });
  const published =
    weatherQuery.data?.availability === "published"
      ? weatherQuery.data.observation
      : null;
  // The grid is coarse (one sample per ingest cell), so how far away the sample was taken is
  // part of the reading. Without it a value from the far side of a range reads as measured here.
  const distanceKm =
    published === null
      ? null
      : haversineDistance(
          [center.lon, center.lat],
          [published.lon, published.lat]
        ) / 1000;
  const farFromSample =
    distanceKm !== null && distanceKm > NEAR_ENOUGH_TO_READ_AS_HERE_KM;
  const sampleSub =
    distanceKm === null
      ? "No published observation"
      : `Nearest sample ${distanceKm.toFixed(0)} km away`;

  return (
    <div className="flex flex-col gap-4">
      {fireData.error && !fireData.isLoading && (
        <div className="flex items-center gap-2 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 dark:bg-amber-950/20">
          <AlertTriangle className="h-4 w-4 shrink-0 text-amber-600" />
          <p className="text-xs font-medium text-amber-700 dark:text-amber-400">
            Published fire detections could not be refreshed. Previously loaded data may be stale.
          </p>
        </div>
      )}

      {/* Only when the warehouse actually answered with nothing. The predecessor of this
          block rendered unconditionally, so it asserted an outage over live readings. */}
      {!weatherQuery.isLoading && published === null && (
        <div className="flex items-center gap-2 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 dark:bg-amber-950/20">
          <AlertTriangle aria-hidden="true" className="h-4 w-4 shrink-0 text-amber-600" />
          <p className="text-xs font-medium text-amber-700 dark:text-amber-400">
            No fresh weather observation has been published near this point. No fallback
            reading is shown.
          </p>
        </div>
      )}

      {/* A reading taken far enough away to be other weather. Shown beside the values rather
          than instead of them: the sample is real, it is just not from here. */}
      {farFromSample && (
        <div className="flex items-center gap-2 rounded-lg border border-sky-500/40 bg-sky-500/10 px-3 py-2">
          <MapPin aria-hidden="true" className="h-4 w-4 shrink-0 text-sky-600" />
          <p className="text-xs text-[hsl(var(--foreground))]">
            The nearest published sample is {distanceKm?.toFixed(0)} km away — these readings
            describe that grid point, not this one.
          </p>
        </div>
      )}

      <div className="grid grid-cols-2 gap-2">
        <StatCard
          icon={<Flame className="h-3.5 w-3.5" />}
          label="Fire Detections"
          value={fireCountUnsettled ? "..." : effectiveFireCount}
          sub={fireCountSub}
        />
        <StatCard
          icon={<Wind className="h-3.5 w-3.5" />}
          label="Wind Speed"
          value={weatherQuery.isLoading ? "..." : reading(published?.windSpeed, 1, " m/s")}
          sub={sampleSub}
        />
        <StatCard
          icon={<Droplets className="h-3.5 w-3.5" />}
          label="Humidity"
          value={weatherQuery.isLoading ? "..." : reading(published?.humidity, 0, "%")}
          sub={sampleSub}
        />
        <StatCard
          icon={<Flame className="h-3.5 w-3.5" />}
          label="Temperature"
          value={weatherQuery.isLoading ? "..." : reading(published?.temperature, 1, " °C")}
          sub={sampleSub}
        />
      </div>

      {published !== null && (
        <p className="text-[10px] text-[hsl(var(--muted-foreground))] leading-relaxed">
          Observed {published.observedAt.slice(0, 16).replace("T", " ")} UTC at{" "}
          {published.lat.toFixed(2)}, {published.lon.toFixed(2)} — the nearest point on the
          published Open-Meteo grid.
        </p>
      )}
    </div>
  );
}
