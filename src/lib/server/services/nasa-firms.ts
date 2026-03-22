export interface FIRMSFirePoint {
  latitude: number;
  longitude: number;
  brightness: number;
  confidence: string;
  frp: number;
  satellite: string;
  acqDate: string;
  acqTime: string;
}

export interface FireFeatureCollection {
  type: "FeatureCollection";
  features: Array<{
    type: "Feature";
    geometry: { type: "Point"; coordinates: [number, number] };
    properties: {
      brightness: number;
      confidence: string;
      frp: number;
      satellite: string;
      acqDate: string;
      acqTime: string;
    };
  }>;
}

/**
 * Parse NASA FIRMS CSV response into structured fire point records.
 * Expected columns: latitude,longitude,brightness,scan,track,acq_date,acq_time,satellite,confidence,version,bright_t31,frp,daynight
 */
function parseFIRMSCsv(csv: string): FIRMSFirePoint[] {
  const lines = csv.trim().split("\n");
  if (lines.length < 2) return [];

  const header = lines[0].split(",").map((h) => h.trim().toLowerCase());
  const latIdx = header.indexOf("latitude");
  const lonIdx = header.indexOf("longitude");
  const brightnessIdx = header.indexOf("brightness");
  const confidenceIdx = header.indexOf("confidence");
  const frpIdx = header.indexOf("frp");
  const satelliteIdx = header.indexOf("satellite");
  const acqDateIdx = header.indexOf("acq_date");
  const acqTimeIdx = header.indexOf("acq_time");

  const points: FIRMSFirePoint[] = [];

  for (let i = 1; i < lines.length; i++) {
    const cols = lines[i].split(",");
    if (cols.length < 4) continue;

    const lat = parseFloat(cols[latIdx]);
    const lon = parseFloat(cols[lonIdx]);
    if (isNaN(lat) || isNaN(lon)) continue;

    points.push({
      latitude: lat,
      longitude: lon,
      brightness: brightnessIdx >= 0 ? parseFloat(cols[brightnessIdx]) || 0 : 0,
      confidence: confidenceIdx >= 0 ? cols[confidenceIdx].trim() : "nominal",
      frp: frpIdx >= 0 ? parseFloat(cols[frpIdx]) || 0 : 0,
      satellite: satelliteIdx >= 0 ? cols[satelliteIdx].trim() : "VIIRS",
      acqDate: acqDateIdx >= 0 ? cols[acqDateIdx].trim() : "",
      acqTime: acqTimeIdx >= 0 ? cols[acqTimeIdx].trim() : "",
    });
  }

  return points;
}

/**
 * Fetch active fires from NASA FIRMS VIIRS SNPP NRT dataset.
 * @param bbox - Optional bounding box "west,south,east,north" (default world)
 * @param dayRange - Number of days back to fetch (1-10, default 1)
 */
export async function fetchActiveFiresNASA(
  bbox?: string,
  dayRange: number = 1
): Promise<FireFeatureCollection> {
  const apiKey = process.env.NASA_FIRMS_KEY;
  if (!apiKey) {
    throw new Error("NASA_FIRMS_KEY environment variable is not set");
  }

  const area = bbox ?? "-180,-90,180,90";
  const clampedDayRange = Math.min(10, Math.max(1, dayRange));

  const url = `https://firms.modaps.eosdis.nasa.gov/api/area/csv/${apiKey}/VIIRS_SNPP_NRT/${area}/${clampedDayRange}`;

  const response = await fetch(url, {
    headers: { Accept: "text/csv" },
    next: { revalidate: 3600 },
  });

  if (!response.ok) {
    throw new Error(
      `NASA FIRMS API error: ${response.status} ${response.statusText}`
    );
  }

  const csv = await response.text();
  const points = parseFIRMSCsv(csv);

  const featureCollection: FireFeatureCollection = {
    type: "FeatureCollection",
    features: points.map((p) => ({
      type: "Feature",
      geometry: {
        type: "Point",
        coordinates: [p.longitude, p.latitude],
      },
      properties: {
        brightness: p.brightness,
        confidence: p.confidence,
        frp: p.frp,
        satellite: p.satellite,
        acqDate: p.acqDate,
        acqTime: p.acqTime,
      },
    })),
  };

  return featureCollection;
}
