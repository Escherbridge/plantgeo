export interface FireRiskParams {
  vegetationType: string;
  slope: number;
  aspect: number;
  humidity: number;
  windSpeed: number;
  fuelLoadFactor?: number;
}

// Vegetation fuel load weights (0-1 scale)
const VEGETATION_FUEL_WEIGHTS: Record<string, number> = {
  chaparral: 0.95,
  grassland: 0.8,
  shrubland: 0.85,
  mixed_forest: 0.7,
  conifer_forest: 0.75,
  deciduous_forest: 0.5,
  wetland: 0.1,
  agricultural: 0.4,
  urban: 0.2,
  bare: 0.05,
};

/**
 * Calculate composite fire risk score (0-100) from terrain and weather parameters.
 * Weights: vegetation 30%, slope 20%, aspect 10%, humidity 25%, wind 15%
 */
export function calculateFireRisk(params: FireRiskParams): number {
  const { vegetationType, slope, aspect, humidity, windSpeed, fuelLoadFactor } = params;

  // Vegetation fuel load factor (0-1)
  const vegetationScore =
    (fuelLoadFactor ?? VEGETATION_FUEL_WEIGHTS[vegetationType] ?? 0.5) * 100;

  // Slope factor: steeper slopes spread fire faster (0-90 degrees -> 0-100)
  const slopeScore = Math.min(100, (slope / 45) * 100);

  // Aspect factor: south/southwest facing slopes are drier (0-360 degrees)
  // South = 180 degrees = max risk, North = 0/360 = min risk
  const aspectRad = (aspect * Math.PI) / 180;
  const aspectScore = ((Math.cos(aspectRad - Math.PI) + 1) / 2) * 100;

  // Humidity factor: lower humidity = higher risk (0-100% -> inverted)
  const humidityScore = Math.max(0, 100 - humidity);

  // Wind speed factor: higher wind = higher risk (m/s, cap at 30 m/s)
  const windScore = Math.min(100, (windSpeed / 30) * 100);

  // Weighted composite score
  const score =
    vegetationScore * 0.3 +
    slopeScore * 0.2 +
    aspectScore * 0.1 +
    humidityScore * 0.25 +
    windScore * 0.15;

  return Math.round(Math.min(100, Math.max(0, score)));
}
