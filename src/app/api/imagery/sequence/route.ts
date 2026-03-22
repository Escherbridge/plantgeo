import { NextRequest, NextResponse } from "next/server";
import { getSequence } from "@/lib/server/services/mapillary";

export async function GET(request: NextRequest) {
  const sequenceId = request.nextUrl.searchParams.get("sequenceId");

  if (!sequenceId) {
    return NextResponse.json({ error: "Missing required param: sequenceId" }, { status: 400 });
  }

  const images = await getSequence(sequenceId);
  return NextResponse.json(images);
}
