export interface FuelBehaviorParams {
  rateOfSpread: number;
  flameLengthFt: number;
  heatPerUnitArea: number;
  fuelLoadFactor: number;
}

export interface LandFireEVT {
  evtCode: number;
  evtName: string;
  fuelParams: FuelBehaviorParams;
}

// Major LANDFIRE EVT fuel model groups → fire behavior parameters
// Groups based on Scott & Burgan 40 fire behavior fuel models + NB (non-burnable)
const EVT_FUEL_PARAMS: Array<{ min: number; max: number; params: FuelBehaviorParams }> = [
  // NB — Non-burnable (91-99)
  { min: 91, max: 99, params: { rateOfSpread: 0, flameLengthFt: 0, heatPerUnitArea: 0, fuelLoadFactor: 0.05 } },
  // GR — Grass (101-109)
  { min: 101, max: 109, params: { rateOfSpread: 60, flameLengthFt: 2.5, heatPerUnitArea: 2000, fuelLoadFactor: 0.8 } },
  // GS — Grass-Shrub (121-124)
  { min: 121, max: 124, params: { rateOfSpread: 45, flameLengthFt: 3.5, heatPerUnitArea: 3500, fuelLoadFactor: 0.82 } },
  // SH — Shrub (141-149)
  { min: 141, max: 149, params: { rateOfSpread: 35, flameLengthFt: 5.0, heatPerUnitArea: 5500, fuelLoadFactor: 0.85 } },
  // TU — Timber Understory (161-165)
  { min: 161, max: 165, params: { rateOfSpread: 20, flameLengthFt: 3.0, heatPerUnitArea: 4000, fuelLoadFactor: 0.72 } },
  // TL — Timber Litter (181-189)
  { min: 181, max: 189, params: { rateOfSpread: 15, flameLengthFt: 2.0, heatPerUnitArea: 3000, fuelLoadFactor: 0.75 } },
  // SB — Slash-Blowdown (201-204)
  { min: 201, max: 204, params: { rateOfSpread: 10, flameLengthFt: 6.0, heatPerUnitArea: 8000, fuelLoadFactor: 0.9 } },
];

const DEFAULT_PARAMS: FuelBehaviorParams = {
  rateOfSpread: 20,
  flameLengthFt: 3.0,
  heatPerUnitArea: 3500,
  fuelLoadFactor: 0.5,
};

function getFuelParamsByCode(evtCode: number): FuelBehaviorParams {
  for (const entry of EVT_FUEL_PARAMS) {
    if (evtCode >= entry.min && evtCode <= entry.max) {
      return entry.params;
    }
  }
  return DEFAULT_PARAMS;
}

const LANDFIRE_IDENTIFY_URL =
  "https://www.landfire.gov/arcgis/rest/services/Landfire/US_200/MapServer/identify";

export async function getLandFireEVT(lat: number, lon: number): Promise<LandFireEVT> {
  const params = new URLSearchParams({
    geometry: `${lon},${lat}`,
    geometryType: "esriGeometryPoint",
    sr: "4326",
    layers: "visible",
    tolerance: "2",
    mapExtent: `${lon - 0.001},${lat - 0.001},${lon + 0.001},${lat + 0.001}`,
    imageDisplay: "256,256,96",
    returnGeometry: "false",
    f: "json",
  });

  const response = await fetch(`${LANDFIRE_IDENTIFY_URL}?${params}`, {
    next: { revalidate: 86400 },
  });

  if (!response.ok) {
    throw new Error(`LANDFIRE API error: ${response.status} ${response.statusText}`);
  }

  const data = (await response.json()) as {
    results?: Array<{ attributes?: { EVT_NAME?: string; Value?: number | string } }>;
  };

  const result = data.results?.[0];
  const evtCode = result?.attributes?.Value ? Number(result.attributes.Value) : 0;
  const evtName = result?.attributes?.EVT_NAME ?? "Unknown";

  return {
    evtCode,
    evtName,
    fuelParams: getFuelParamsByCode(evtCode),
  };
}
