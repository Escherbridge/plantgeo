import { createHmac } from "crypto";
import { db } from "@/lib/server/db";
import { priorityZones } from "@/lib/server/db/schema";

const PLANTCOMMERCE_WEBHOOK_URL = process.env.PLANTCOMMERCE_WEBHOOK_URL;
const PLANTCOMMERCE_WEBHOOK_SECRET = process.env.PLANTCOMMERCE_WEBHOOK_SECRET;

export interface PriorityZoneWebhookPayload {
  event: "priority_zones_updated";
  zones: Array<{
    zoneId: string;
    strategyType: string;
    requestCount: number;
    totalVotes: number;
    bbox: [number, number, number, number] | null;
    centroidLat: number | null;
    centroidLon: number | null;
    locationName: string | null;
  }>;
  timestamp: string;
}

/**
 * Compute a bounding box from a GeoJSON geometry stored in priority_zones.geojson.
 * Returns [minLon, minLat, maxLon, maxLat] or null when the geometry is unavailable.
 */
function bboxFromGeojson(
  geojson: unknown
): [number, number, number, number] | null {
  if (!geojson || typeof geojson !== "object") return null;
  const g = geojson as { type?: string; coordinates?: unknown };
  if (!g.coordinates) return null;

  // Flatten all coordinate pairs regardless of geometry type
  const coords: number[][] = [];
  function collect(c: unknown): void {
    if (!Array.isArray(c)) return;
    if (typeof c[0] === "number") {
      coords.push(c as number[]);
    } else {
      for (const item of c) collect(item);
    }
  }
  collect(g.coordinates);

  if (coords.length === 0) return null;

  let minLon = Infinity;
  let minLat = Infinity;
  let maxLon = -Infinity;
  let maxLat = -Infinity;

  for (const [lng, lt] of coords) {
    if (lng < minLon) minLon = lng;
    if (lng > maxLon) maxLon = lng;
    if (lt < minLat) minLat = lt;
    if (lt > maxLat) maxLat = lt;
  }

  return [minLon, minLat, maxLon, maxLat];
}

/**
 * Sign the webhook payload with HMAC-SHA256 using PLANTCOMMERCE_WEBHOOK_SECRET.
 * Returns the X-Signature header value: "sha256=<hex>".
 */
function signPayload(body: string, secret: string): string {
  const hmac = createHmac("sha256", secret).update(body).digest("hex");
  return `sha256=${hmac}`;
}

/**
 * Dispatch a webhook POST to PLANTCOMMERCE_WEBHOOK_URL with all current priority zones.
 *
 * This function is called after priority zone recomputation (e.g. from recomputePriorityZones).
 * It is safe to call without a queue — runs inline and resolves when the HTTP call completes.
 *
 * When used with BullMQ, import and call `dispatchPriorityZoneWebhook` as the job processor:
 *   worker.process('priority-zone-webhook', dispatchPriorityZoneWebhook);
 */
export async function dispatchPriorityZoneWebhook(): Promise<void> {
  if (!PLANTCOMMERCE_WEBHOOK_URL) {
    // Not configured — skip silently
    return;
  }

  // Fetch all current priority zones
  const zones = await db
    .select({
      id: priorityZones.id,
      strategyType: priorityZones.strategyType,
      requestCount: priorityZones.requestCount,
      totalVotes: priorityZones.totalVotes,
      centroidLat: priorityZones.centroidLat,
      centroidLon: priorityZones.centroidLon,
      geojson: priorityZones.geojson,
    })
    .from(priorityZones);

  const payload: PriorityZoneWebhookPayload = {
    event: "priority_zones_updated",
    zones: zones.map((zone) => ({
      zoneId: zone.id,
      strategyType: zone.strategyType,
      requestCount: zone.requestCount,
      totalVotes: zone.totalVotes,
      bbox: bboxFromGeojson(zone.geojson),
      centroidLat: zone.centroidLat,
      centroidLon: zone.centroidLon,
      locationName: null, // Reverse geocoding is out of scope for v1
    })),
    timestamp: new Date().toISOString(),
  };

  const body = JSON.stringify(payload);

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };

  if (PLANTCOMMERCE_WEBHOOK_SECRET) {
    headers["X-Signature"] = signPayload(body, PLANTCOMMERCE_WEBHOOK_SECRET);
  }

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 10_000);

  try {
    const response = await fetch(PLANTCOMMERCE_WEBHOOK_URL, {
      method: "POST",
      headers,
      body,
      signal: controller.signal,
    });

    if (!response.ok) {
      console.error(
        `[priority-zone-webhook] Webhook delivery failed: ${response.status} ${response.statusText}`
      );
    }
  } catch (err) {
    console.error(`[priority-zone-webhook] Webhook delivery error:`, err);
  } finally {
    clearTimeout(timeoutId);
  }
}
