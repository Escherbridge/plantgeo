import { describe, expect, it } from "vitest";
import { WebGPUAccelerator } from "@/lib/map/webgpu-accelerator";
import { processGeoJsonFeatures, releaseBuffer } from "@/workers/layer-processor.worker";

describe("WebGPU Accelerator & Worker Pipeline", () => {
  it("reports correct support state when navigator.gpu is missing", () => {
    const accelerator = new WebGPUAccelerator();
    expect(accelerator.isSupported()).toBe(false);

    const state = accelerator.getState();
    expect(state.isSupported).toBe(false);
    expect(state.isFallbackActive).toBe(false);
  });

  it("handles initialization fallback gracefully when navigator.gpu is absent", async () => {
    const accelerator = new WebGPUAccelerator();
    const initialized = await accelerator.initialize();
    expect(initialized).toBe(false);

    const state = accelerator.getState();
    expect(state.isFallbackActive).toBe(true);
  });

  it("executes zero-copy WebGL fallback color-ramp mapping", () => {
    const accelerator = new WebGPUAccelerator();
    const packedData = new Float32Array([
      -122.4, 37.7, 10, 0,
      -122.5, 37.8, 50, 1,
      -122.6, 37.9, 90, 2,
    ]);

    const result = accelerator.processWebGLFallback(packedData, { minVal: 0, maxVal: 100, opacity: 0.8 });
    expect(result.length).toBe(12); // 3 features * 4 RGBA floats
    expect(result[3]).toBeCloseTo(0.8); // opacity
    expect(result[7]).toBeCloseTo(0.8);
    expect(result[11]).toBeCloseTo(0.8);
  });

  it("decodes GeoJSON features into packed Float32Array buffers", () => {
    const geojson = {
      type: "FeatureCollection",
      features: [
        {
          type: "Feature",
          geometry: { type: "Point", coordinates: [-120.5, 47.2] },
          properties: { val: 42 },
        },
        {
          type: "Feature",
          geometry: { type: "Point", coordinates: [-121.0, 48.0] },
          properties: { val: 84 },
        },
      ],
    };

    const { packed, count } = processGeoJsonFeatures(geojson);
    expect(count).toBe(2);
    expect(packed.length).toBe(8); // 2 features * 4 floats
    expect(packed[0]).toBeCloseTo(-120.5);
    expect(packed[1]).toBeCloseTo(47.2);
    expect(packed[2]).toBeCloseTo(42);
    expect(packed[4]).toBeCloseTo(-121.0);
    expect(packed[5]).toBeCloseTo(48.0);
    expect(packed[6]).toBeCloseTo(84);

    releaseBuffer(packed.buffer);
  });

  it("handles empty or invalid GeoJSON gracefully", () => {
    const { packed, count } = processGeoJsonFeatures(null);
    expect(count).toBe(0);
    expect(packed.length).toBe(0);
  });
});
