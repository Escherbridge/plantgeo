import { NextResponse } from "next/server";

// --- In-memory cache with TTL ---
let cachedData: GeoJSON.FeatureCollection | null = null;
let cacheTimestamp = 0;
const CACHE_TTL_MS = 60_000; // 60 seconds (NIFC updates every 60s)

const NIFC_INCIDENTS_URL =
  "https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/WFIGS_Incident_Locations_Current/FeatureServer/0/query";

const QUERY_PARAMS = {
  where: "IncidentTypeCategory='WF' AND ActiveFireCandidate=1",
  outFields: [
    "IncidentName",
    "IncidentSize",
    "POOState",
    "FireDiscoveryDateTime",
    "PercentContained",
    "UniqueFireIdentifier",
    "GACC",
    "FireCause",
    "TotalIncidentPersonnel",
  ].join(","),
  f: "geojson",
  resultRecordCount: "2000",
};

/** Fetch all pages of fire incidents from NIFC ArcGIS. */
async function fetchAllFireIncidents(): Promise<GeoJSON.FeatureCollection> {
  const allFeatures: GeoJSON.Feature[] = [];
  let offset = 0;
  let hasMore = true;

  while (hasMore) {
    const params = new URLSearchParams({
      ...QUERY_PARAMS,
      resultOffset: String(offset),
    });

    const res = await fetch(`${NIFC_INCIDENTS_URL}?${params}`, {
      next: { revalidate: 60 },
    });

    if (!res.ok) {
      throw new Error(`NIFC API error: ${res.status}`);
    }

    const data = await res.json();

    if (data.features && Array.isArray(data.features)) {
      allFeatures.push(...data.features);
    }

    // ArcGIS signals more pages via exceededTransferLimit
    hasMore = data.exceededTransferLimit === true;
    offset += 2000;

    // Safety: cap at 10 pages (20,000 features)
    if (offset > 20000) break;
  }

  return {
    type: "FeatureCollection",
    features: allFeatures,
  };
}

export async function GET() {
  try {
    const now = Date.now();

    // Return cached if fresh
    if (cachedData && now - cacheTimestamp < CACHE_TTL_MS) {
      return NextResponse.json(cachedData, {
        headers: {
          "Cache-Control": "public, s-maxage=60, stale-while-revalidate=120",
          "X-Fire-Data-Source": "cache",
          "X-Fire-Count": String(cachedData.features.length),
        },
      });
    }

    // Fetch fresh data
    const data = await fetchAllFireIncidents();
    cachedData = data;
    cacheTimestamp = now;

    return NextResponse.json(data, {
      headers: {
        "Cache-Control": "public, s-maxage=60, stale-while-revalidate=120",
        "X-Fire-Data-Source": "nifc",
        "X-Fire-Count": String(data.features.length),
      },
    });
  } catch (error) {
    // On error, return cached data if available (even if stale)
    if (cachedData) {
      return NextResponse.json(cachedData, {
        headers: {
          "X-Fire-Data-Source": "stale-cache",
          "X-Fire-Count": String(cachedData.features.length),
        },
      });
    }

    return NextResponse.json(
      { error: "Failed to fetch fire data" },
      { status: 502 },
    );
  }
}
