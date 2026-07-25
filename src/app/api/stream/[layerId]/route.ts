import { NextRequest, NextResponse } from "next/server";
import { and, eq } from "drizzle-orm";
import { getServerSession } from "@/lib/server/auth";
import { db } from "@/lib/server/db";
import { layers, teamMembers } from "@/lib/server/db/schema";
import { subscribe, unsubscribe } from "@/lib/server/services/realtime";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const LAYER_REFERENCE_PATTERN = /^(?:[0-9a-f-]{36}|[a-z0-9][a-z0-9_-]{0,99})$/i;
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const MAX_EVENT_BYTES = 256 * 1024;
const activeConnectionsByUser = new Map<string, number>();
let activeConnections = 0;

function boundedEnvironmentInteger(
  name: string,
  fallback: number,
  maximum: number
): number {
  const value = Number(process.env[name]);
  return Number.isInteger(value) && value > 0
    ? Math.min(value, maximum)
    : fallback;
}

function acquireConnection(userId: string): boolean {
  const maxConnections = boundedEnvironmentInteger(
    "SSE_MAX_CONNECTIONS_PER_REPLICA",
    100,
    1_000
  );
  const maxPerUser = boundedEnvironmentInteger(
    "SSE_MAX_CONNECTIONS_PER_USER",
    5,
    50
  );
  const userConnections = activeConnectionsByUser.get(userId) ?? 0;
  if (activeConnections >= maxConnections || userConnections >= maxPerUser) {
    return false;
  }
  activeConnections += 1;
  activeConnectionsByUser.set(userId, userConnections + 1);
  return true;
}

function releaseConnection(userId: string): void {
  const userConnections = activeConnectionsByUser.get(userId) ?? 0;
  if (userConnections <= 1) activeConnectionsByUser.delete(userId);
  else activeConnectionsByUser.set(userId, userConnections - 1);
  activeConnections = Math.max(0, activeConnections - 1);
}

export function alertStreamChannel(
  streamReference: string,
  userId: string
): string | null {
  if (streamReference === "alerts") return `alerts:${userId}`;
  if (streamReference === "alerts:global") return "alerts:global";
  return null;
}

async function authorizedLayerChannel(
  layerReference: string,
  userId: string,
  platformRole: string | undefined
): Promise<{ status: 200; channel: string } | { status: 400 | 403 | 404 }> {
  if (!LAYER_REFERENCE_PATTERN.test(layerReference)) return { status: 400 };

  const [layer] = await db
    .select({
      id: layers.id,
      name: layers.name,
      isPublic: layers.isPublic,
      teamId: layers.teamId,
    })
    .from(layers)
    .where(
      UUID_PATTERN.test(layerReference)
        ? eq(layers.id, layerReference)
        : eq(layers.name, layerReference)
    )
    .limit(1);

  if (!layer) return { status: 404 };
  if (!layer.isPublic && platformRole !== "admin") {
    if (!layer.teamId) return { status: 403 };
    const [membership] = await db
      .select({ userId: teamMembers.userId })
      .from(teamMembers)
      .where(
        and(
          eq(teamMembers.teamId, layer.teamId),
          eq(teamMembers.userId, userId)
        )
      )
      .limit(1);
    if (!membership) return { status: 403 };
  }

  return { status: 200, channel: `layer:${layer.name}` };
}

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ layerId: string }> }
) {
  const session = await getServerSession();
  const user = session?.user as
    | { id?: string; platformRole?: string }
    | undefined;
  if (!user?.id) {
    return NextResponse.json({ error: "Authentication required" }, { status: 401 });
  }
  const userId = user.id;

  const { layerId } = await params;
  const alertChannel = alertStreamChannel(layerId, userId);
  const authorization = alertChannel
    ? { status: 200 as const, channel: alertChannel }
    : await authorizedLayerChannel(layerId, userId, user.platformRole);
  if (authorization.status !== 200) {
    const error =
      authorization.status === 400
        ? "Invalid stream reference"
        : authorization.status === 404
          ? "Layer not found"
          : "Layer access denied";
    return NextResponse.json({ error }, { status: authorization.status });
  }
  const channel = authorization.channel;

  if (!acquireConnection(userId)) {
    return NextResponse.json(
      { error: "Too many live connections" },
      { status: 429, headers: { "Retry-After": "15" } }
    );
  }

  const encoder = new TextEncoder();
  let eventId = 0;
  let intervalId: ReturnType<typeof setInterval> | undefined;
  let controllerRef: ReadableStreamDefaultController<Uint8Array> | null = null;
  let closed = false;
  let subscribed = false;

  const callback = (data: unknown) => {
    if (closed || !controllerRef) return;
    try {
      const payload = JSON.stringify(data);
      const encoded = encoder.encode(
        `id: ${++eventId}\nevent: feature\ndata: ${payload}\n\n`
      );
      if (encoded.byteLength > MAX_EVENT_BYTES) return;
      controllerRef.enqueue(encoded);
    } catch {
      cleanup();
    }
  };

  function cleanup(): void {
    if (closed) return;
    closed = true;
    if (intervalId) clearInterval(intervalId);
    if (subscribed) void unsubscribe(channel, callback);
    releaseConnection(userId);
  }

  try {
    await subscribe(channel, callback);
    subscribed = true;
  } catch {
    cleanup();
    return NextResponse.json(
      { error: "Live updates are temporarily unavailable" },
      {
        status: 503,
        headers: { "Cache-Control": "no-store", "Retry-After": "30" },
      }
    );
  }

  if (request.signal.aborted) {
    cleanup();
    return NextResponse.json(
      { error: "Live connection ended before setup completed" },
      { status: 408, headers: { "Cache-Control": "no-store" } }
    );
  }

  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controllerRef = controller;
      controller.enqueue(
        encoder.encode(
          `event: connected\ndata: ${JSON.stringify({ stream: layerId, resumable: false })}\n\n`
        )
      );

      intervalId = setInterval(() => {
        if (closed) return;
        try {
          controller.enqueue(encoder.encode(": heartbeat\n\n"));
        } catch {
          cleanup();
        }
      }, 30_000);
    },
    cancel() {
      cleanup();
      controllerRef = null;
    },
  });

  request.signal.addEventListener(
    "abort",
    () => {
      cleanup();
      if (controllerRef) {
        try {
          controllerRef.close();
        } catch {
          // The stream may already be closed by the runtime.
        }
        controllerRef = null;
      }
    },
    { once: true }
  );

  return new Response(stream, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-store, no-transform",
      Connection: "keep-alive",
      Vary: "Cookie",
      "X-Accel-Buffering": "no",
    },
  });
}
