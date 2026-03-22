import { NextRequest, NextResponse } from "next/server";
import { getImageById } from "@/lib/server/services/mapillary";

export async function GET(request: NextRequest) {
  const id = request.nextUrl.searchParams.get("id");

  if (!id) {
    return NextResponse.json({ error: "Missing required param: id" }, { status: 400 });
  }

  const image = await getImageById(id);
  return NextResponse.json(image);
}
