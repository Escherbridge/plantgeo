import { NextResponse } from "next/server";
import { forecastQuerySchema } from "@/lib/weather-forecast/contract";
import { readWeatherForecast } from "@/lib/weather-forecast/server";

export async function GET(request: Request) {
  const params = new URL(request.url).searchParams;
  const parsed = forecastQuerySchema.safeParse(Object.fromEntries(params));
  if (!parsed.success || new Set(params.keys()).size !== [...params.keys()].length) {
    return NextResponse.json({ status: "refused", reason: "Supply one run, selected latitude/longitude and a UTC hourly window of at most 48 hours." }, { status: 400 });
  }
  try {
    const data = await readWeatherForecast(parsed.data, request.signal);
    return NextResponse.json(data, { headers: { "Cache-Control": "no-store" } });
  } catch {
    return NextResponse.json({ status: "upstream-unavailable", reason: "The local governed forecast plane is disabled, unavailable or failed contract validation. No forecast values were substituted." }, { status: 503, headers: { "Cache-Control": "no-store" } });
  }
}
