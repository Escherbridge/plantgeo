import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/server/db";
import { layers } from "@/lib/server/db/schema";
import {
  apiKeyAuthorizationErrorResponse,
  authorizeApiRequest,
} from "@/lib/server/middleware/api-auth";
import { layerVisibilityCondition } from "@/lib/server/security/layer-access";

export async function GET(request: NextRequest) {
  const authResult = await authorizeApiRequest(request, "read:layers");
  if (!authResult.valid) {
    return apiKeyAuthorizationErrorResponse(authResult);
  }

  const visibleLayers = await db
    .select({
      id: layers.id,
      name: layers.name,
      type: layers.type,
      description: layers.description,
    })
    .from(layers)
    .where(
      layerVisibilityCondition({
        userId: authResult.userId,
        teamId: authResult.teamId,
      })
    );

  return NextResponse.json(visibleLayers);
}
