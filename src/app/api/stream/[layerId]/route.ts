import { NextRequest } from "next/server";
import { subscribe, unsubscribe } from "@/lib/server/services/realtime";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ layerId: string }> }
) {
  const { layerId } = await params;
  const channel = `layer:${layerId}`;
  const lastEventId = request.headers.get("Last-Event-ID") ?? undefined;

  let eventId = lastEventId ? Number(lastEventId) : 0;

  const encoder = new TextEncoder();
  let intervalId: ReturnType<typeof setInterval>;
  let callback: (data: unknown) => void;
  let controllerRef: ReadableStreamDefaultController<Uint8Array> | null = null;

  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      controllerRef = controller;

      // Send initial connection event
      controller.enqueue(
        encoder.encode(`event: connected\ndata: {"layerId":"${layerId}"}\n\n`)
      );

      // Heartbeat every 30s to keep connection alive
      intervalId = setInterval(() => {
        try {
          controller.enqueue(encoder.encode(`: heartbeat\n\n`));
        } catch {
          // Controller may be closed
        }
      }, 30_000);

      // Subscribe to Redis channel
      callback = (data: unknown) => {
        eventId += 1;
        try {
          const payload = JSON.stringify(data);
          controller.enqueue(
            encoder.encode(`id: ${eventId}\nevent: feature\ndata: ${payload}\n\n`)
          );
        } catch {
          // Controller may be closed
        }
      };

      await subscribe(channel, callback);
    },

    cancel() {
      clearInterval(intervalId);
      if (callback) {
        unsubscribe(channel, callback).catch(() => undefined);
      }
      controllerRef = null;
    },
  });

  // Clean up if client disconnects
  request.signal.addEventListener("abort", () => {
    clearInterval(intervalId);
    if (callback) {
      unsubscribe(channel, callback).catch(() => undefined);
    }
    if (controllerRef) {
      try {
        controllerRef.close();
      } catch {
        // Already closed
      }
      controllerRef = null;
    }
  });

  return new Response(stream, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
      "X-Accel-Buffering": "no",
    },
  });
}
