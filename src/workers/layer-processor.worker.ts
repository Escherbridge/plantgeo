/**
 * Dedicated Web Worker Data Engine for offloading GeoJSON parsing, array unpacking,
 * and feature filtering off the main thread into zero-copy Float32Array Transferables.
 */

export interface ProcessGeoJsonMessage {
  type: "PROCESS_GEOJSON";
  layerId: string;
  geojson: unknown;
  valueProperty?: string;
}

interface BufferPoolEntry {
  buffer: ArrayBuffer;
  byteLength: number;
}

// Memory pool of reusable ArrayBuffers to prevent GC pauses
const MAX_POOL_SIZE = 10;
const bufferPool: BufferPoolEntry[] = [];

function acquireBuffer(minBytes: number): ArrayBuffer {
  const index = bufferPool.findIndex((b) => b.byteLength >= minBytes);
  if (index !== -1) {
    const entry = bufferPool.splice(index, 1)[0];
    return entry.buffer;
  }
  return new ArrayBuffer(Math.max(minBytes, 1024 * 64));
}

export function releaseBuffer(buffer: ArrayBufferLike): void {
  if (bufferPool.length < MAX_POOL_SIZE && buffer instanceof ArrayBuffer) {
    bufferPool.push({ buffer, byteLength: buffer.byteLength });
  }
}

/**
 * Extracts coordinates [lng, lat, value, index] into a packed contiguous Float32Array.
 */
export function processGeoJsonFeatures(
  geojson: unknown,
  valueProperty = "val"
): { packed: Float32Array; count: number } {
  if (!geojson || typeof geojson !== "object" || !("features" in geojson) || !Array.isArray((geojson as { features: unknown }).features)) {
    return { packed: new Float32Array(0), count: 0 };
  }

  const features = (geojson as { features: Array<Record<string, unknown>> }).features;
  const count = features.length;
  const floatsPerFeature = 4; // [lng, lat, value, index]
  const requiredBytes = count * floatsPerFeature * 4;

  const rawBuffer = acquireBuffer(requiredBytes);
  const packed = new Float32Array(rawBuffer, 0, count * floatsPerFeature);

  for (let i = 0; i < count; i++) {
    const f = features[i] || {};
    const geometry = f.geometry as { coordinates?: unknown } | undefined;
    const coords = geometry?.coordinates;
    const props = (f.properties as Record<string, unknown> | undefined) || {};

    let lng = 0;
    let lat = 0;

    if (Array.isArray(coords)) {
      if (typeof coords[0] === "number" && typeof coords[1] === "number") {
        lng = coords[0];
        lat = coords[1];
      } else if (Array.isArray(coords[0]) && Array.isArray(coords[0][0])) {
        const p0 = coords[0][0] as number[];
        lng = p0[0] || 0;
        lat = p0[1] || 0;
      }
    }

    const valProp = props[valueProperty];
    const val = typeof valProp === "number" ? valProp : (props.val as number) || (props.value as number) || 0;

    const offset = i * floatsPerFeature;
    packed[offset] = lng;
    packed[offset + 1] = lat;
    packed[offset + 2] = val;
    packed[offset + 3] = i;
  }

  return { packed, count };
}

if (typeof self !== "undefined" && typeof self.addEventListener === "function") {
  self.addEventListener("message", (event: MessageEvent<ProcessGeoJsonMessage>) => {
    const { type, layerId, geojson, valueProperty } = event.data || {};

    if (type === "PROCESS_GEOJSON") {
      const { packed, count } = processGeoJsonFeatures(geojson, valueProperty);
      // Transfer the underlying ArrayBuffer back to main thread with zero-copy
      const ctx = self as unknown as { postMessage: (msg: unknown, transfer: Transferable[]) => void };
      ctx.postMessage(
        {
          type: "PROCESSED_LAYER",
          layerId,
          buffer: packed.buffer,
          count,
          floatsPerFeature: 4,
        },
        [packed.buffer]
      );
    }
  });
}
