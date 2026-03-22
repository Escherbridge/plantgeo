export interface CurrentWeather {
  temperature: number;
  humidity: number;
  windSpeed: number;
  windDirection: number;
  precipitation: number;
}

export interface HourlyForecast {
  time: string[];
  temperature: number[];
  windSpeed: number[];
}

export interface WeatherForecast {
  current: CurrentWeather;
  hourly: HourlyForecast;
}

interface OpenMeteoCurrentResponse {
  current: {
    temperature_2m: number;
    relative_humidity_2m: number;
    wind_speed_10m: number;
    wind_direction_10m: number;
    precipitation: number;
  };
}

interface OpenMeteoForecastResponse extends OpenMeteoCurrentResponse {
  hourly: {
    time: string[];
    temperature_2m: number[];
    wind_speed_10m: number[];
  };
}

const BASE_URL = "https://api.open-meteo.com/v1/forecast";

/**
 * Fetch current weather conditions from Open-Meteo (free, no API key required).
 */
export async function getCurrentWeather(
  lat: number,
  lon: number
): Promise<CurrentWeather> {
  const params = new URLSearchParams({
    latitude: String(lat),
    longitude: String(lon),
    current:
      "temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m,precipitation",
    wind_speed_unit: "ms",
  });

  const response = await fetch(`${BASE_URL}?${params}`, {
    next: { revalidate: 1800 },
  });

  if (!response.ok) {
    throw new Error(
      `Open-Meteo API error: ${response.status} ${response.statusText}`
    );
  }

  const data = (await response.json()) as OpenMeteoCurrentResponse;
  const c = data.current;

  return {
    temperature: c.temperature_2m,
    humidity: c.relative_humidity_2m,
    windSpeed: c.wind_speed_10m,
    windDirection: c.wind_direction_10m,
    precipitation: c.precipitation,
  };
}

/**
 * Fetch current weather + 7-day hourly forecast from Open-Meteo.
 */
export async function getForecast(
  lat: number,
  lon: number
): Promise<WeatherForecast> {
  const params = new URLSearchParams({
    latitude: String(lat),
    longitude: String(lon),
    current:
      "temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m,precipitation",
    hourly: "temperature_2m,wind_speed_10m",
    wind_speed_unit: "ms",
    forecast_days: "7",
  });

  const response = await fetch(`${BASE_URL}?${params}`, {
    next: { revalidate: 3600 },
  });

  if (!response.ok) {
    throw new Error(
      `Open-Meteo API error: ${response.status} ${response.statusText}`
    );
  }

  const data = (await response.json()) as OpenMeteoForecastResponse;
  const c = data.current;

  return {
    current: {
      temperature: c.temperature_2m,
      humidity: c.relative_humidity_2m,
      windSpeed: c.wind_speed_10m,
      windDirection: c.wind_direction_10m,
      precipitation: c.precipitation,
    },
    hourly: {
      time: data.hourly.time,
      temperature: data.hourly.temperature_2m,
      windSpeed: data.hourly.wind_speed_10m,
    },
  };
}
