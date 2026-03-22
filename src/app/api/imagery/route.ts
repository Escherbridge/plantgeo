import { NextRequest, NextResponse } from "next/server";
import { getImages } from "@/lib/server/services/mapillary";

export async function GET(request: NextRequest) {
  const params = request.nextUrl.searchParams;

  const west = params.get("west");
  const south = params.get("south");
  const east = params.get("east");
  const north = params.get("north");

  if (!west || !south || !east || !north) {
    return NextResponse.json(
      { error: "Missing required bbox params: west, south, east, north" },
      { status: 400 }
    );
  }

  const bbox = {
    west: Number(west),
    south: Number(south),
    east: Number(east),
    north: Number(north),
  };

  if ([bbox.west, bbox.south, bbox.east, bbox.north].some(isNaN)) {
    return NextResponse.json(
      { error: "bbox params must be valid numbers" },
      { status: 400 }
    );
  }

  const limit = params.has("limit") ? Number(params.get("limit")) : 100;

  const featureCollection = await getImages(bbox, limit);
  return NextResponse.json(featureCollection);
}
