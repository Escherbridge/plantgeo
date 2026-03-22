export interface WaterGauge {
  siteNo: string;
  siteName: string;
  lat: number;
  lon: number;
  flowCfs: number | null;
  percentile: number | null;
  condition: "above_normal" | "normal" | "below_normal" | "low" | "critically_low" | "unknown";
  trend: "rising" | "stable" | "declining" | null;
  updatedAt: string;
}

export interface GroundwaterWell {
  siteNo: string;
  siteName: string;
  lat: number;
  lon: number;
  depthFt: number | null;
  trend: "rising" | "stable" | "declining" | "critical";
  updatedAt: string;
}

interface NWISTimeSeries {
  sourceInfo: {
    siteName: string;
    siteCode: { value: string }[];
    geoLocation: {
      geogLocation: { latitude: number; longitude: number };
    };
  };
  values: {
    value: { value: string; dateTime: string }[];
    qualifier?: { qualifierCode: string }[];
  }[];
  variable: {
    variableCode: { value: string }[];
  };
}

interface NWISResponse {
  value: {
    timeSeries: NWISTimeSeries[];
  };
}

/**
 * Classify streamflow condition based on percentile.
 */
function classifyCondition(
  percentile: number | null
): WaterGauge["condition"] {
  if (percentile === null) return "unknown";
  if (percentile > 75) return "above_normal";
  if (percentile >= 25) return "normal";
  if (percentile >= 10) return "below_normal";
  if (percentile >= 5) return "low";
  return "critically_low";
}

/**
 * Infer a simple trend from qualifier codes provided by NWIS.
 * NWIS qualifier "P" = provisional, "Ice" = ice affected, etc.
 * Without historical comparison we default to stable.
 */
function inferTrend(
  qualifiers?: { qualifierCode: string }[]
): WaterGauge["trend"] {
  if (!qualifiers) return "stable";
  const codes = qualifiers.map((q) => q.qualifierCode.toLowerCase());
  if (codes.includes("e")) return "declining";
  return "stable";
}

/**
 * Fetch active USGS NWIS streamflow gauges for a bounding box.
 * @param bbox "west,south,east,north"
 */
export async function getStreamflowGauges(bbox: string): Promise<WaterGauge[]> {
  const url =
    `https://waterservices.usgs.gov/nwis/iv/?format=json` +
    `&bBox=${bbox}&parameterCd=00060&siteType=ST&siteStatus=active`;

  const response = await fetch(url, {
    headers: { Accept: "application/json" },
    next: { revalidate: 900 }, // 15 minutes
  });

  if (!response.ok) {
    throw new Error(
      `USGS NWIS streamflow error: ${response.status} ${response.statusText}`
    );
  }

  const data = (await response.json()) as NWISResponse;
  const timeSeries = data?.value?.timeSeries ?? [];

  return timeSeries.map((ts): WaterGauge => {
    const siteNo = ts.sourceInfo.siteCode[0]?.value ?? "";
    const siteName = ts.sourceInfo.siteName ?? "";
    const lat = ts.sourceInfo.geoLocation.geogLocation.latitude;
    const lon = ts.sourceInfo.geoLocation.geogLocation.longitude;

    const latestValues = ts.values[0]?.value ?? [];
    const latest = latestValues[latestValues.length - 1];
    const flowCfs = latest ? parseFloat(latest.value) : null;
    const updatedAt = latest?.dateTime ?? new Date().toISOString();

    // NWIS does not return real-time percentile in the IV service;
    // percentile would require a stats service call. We approximate:
    // null means unknown until the BullMQ job enriches the DB record.
    const percentile: number | null = null;

    const qualifiers = ts.values[0]?.qualifier;

    return {
      siteNo,
      siteName,
      lat,
      lon,
      flowCfs: isNaN(flowCfs as number) ? null : flowCfs,
      percentile,
      condition: classifyCondition(percentile),
      trend: inferTrend(qualifiers),
      updatedAt,
    };
  });
}

/**
 * Fetch USGS NWIS groundwater well data for a bounding box.
 * parameterCd=72019 = depth to water level, feet below land surface
 * @param bbox "west,south,east,north"
 */
export async function getGroundwaterWells(
  bbox: string
): Promise<GroundwaterWell[]> {
  const url =
    `https://waterservices.usgs.gov/nwis/iv/?format=json` +
    `&bBox=${bbox}&parameterCd=72019&siteType=GW`;

  const response = await fetch(url, {
    headers: { Accept: "application/json" },
    next: { revalidate: 3600 },
  });

  if (!response.ok) {
    throw new Error(
      `USGS NWIS groundwater error: ${response.status} ${response.statusText}`
    );
  }

  const data = (await response.json()) as NWISResponse;
  const timeSeries = data?.value?.timeSeries ?? [];

  return timeSeries.map((ts): GroundwaterWell => {
    const siteNo = ts.sourceInfo.siteCode[0]?.value ?? "";
    const siteName = ts.sourceInfo.siteName ?? "";
    const lat = ts.sourceInfo.geoLocation.geogLocation.latitude;
    const lon = ts.sourceInfo.geoLocation.geogLocation.longitude;

    const latestValues = ts.values[0]?.value ?? [];
    const latest = latestValues[latestValues.length - 1];
    const depthFt = latest ? parseFloat(latest.value) : null;
    const updatedAt = latest?.dateTime ?? new Date().toISOString();

    // Determine trend by comparing first vs last reading in the series
    let trend: GroundwaterWell["trend"] = "stable";
    if (latestValues.length >= 2) {
      const first = parseFloat(latestValues[0].value);
      const last = parseFloat(latestValues[latestValues.length - 1].value);
      if (!isNaN(first) && !isNaN(last)) {
        // Higher depth value = water table dropping (worse)
        const delta = last - first;
        if (delta > 10) trend = "critical";
        else if (delta > 5) trend = "declining";
        else if (delta < -2) trend = "rising";
      }
    }

    return {
      siteNo,
      siteName,
      lat,
      lon,
      depthFt: depthFt !== null && isNaN(depthFt) ? null : depthFt,
      trend,
      updatedAt,
    };
  });
}
