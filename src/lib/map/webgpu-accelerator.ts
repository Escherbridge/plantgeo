/**
 * WebGPU Hardware Acceleration Manager with WGSL compute shaders for GPU-accelerated
 * layer rendering, color-ramp interpolation, and zero-copy WebGL fallback.
 */

const WGSL_COLOR_RAMP_SHADER = `
struct Feature {
  lng: f32,
  lat: f32,
  val: f32,
  index: f32,
};

struct OutputColor {
  r: f32,
  g: f32,
  b: f32,
  a: f32,
};

struct Uniforms {
  minVal: f32,
  maxVal: f32,
  opacity: f32,
  count: u32,
};

@group(0) @binding(0) var<storage, read> inputs: array<Feature>;
@group(0) @binding(1) var<storage, read_write> outputs: array<OutputColor>;
@group(0) @binding(2) var<uniform> uniforms: Uniforms;

@compute @workgroup_size(64)
fn main(@builtin(global_invocation_id) global_id: vec3<u32>) {
  let index = global_id.x;
  if (index >= uniforms.count) {
    return;
  }

  let feat = inputs[index];
  let norm = clamp((feat.val - uniforms.minVal) / max(uniforms.maxVal - uniforms.minVal, 0.001), 0.0, 1.0);

  // Smooth color ramp: Blue (low) -> Green -> Yellow -> Red (high)
  var r = clamp(2.0 * norm - 0.5, 0.0, 1.0);
  var g = clamp(norm < 0.5 ? 2.0 * norm : 2.0 * (1.0 - norm), 0.0, 1.0);
  var b = clamp(1.0 - 2.0 * norm, 0.0, 1.0);
  var a = uniforms.opacity;

  outputs[index] = OutputColor(r, g, b, a);
}
`;

const GPU_BUFFER_USAGE = {
  STORAGE: 0x0080,
  VERTEX: 0x0008,
  COPY_SRC: 0x0004,
  COPY_DST: 0x0008,
  UNIFORM: 0x0040,
  MAP_READ: 0x0001,
};

const GPU_MAP_MODE = {
  READ: 0x0001,
  WRITE: 0x0002,
};

export interface WebGPUAcceleratorState {
  isSupported: boolean;
  isInitialized: boolean;
  isFallbackActive: boolean;
  activeLayerCount: number;
  deviceLostCount: number;
}

export class WebGPUAccelerator {
  private device: GPUDevice | null = null;
  private adapter: GPUAdapter | null = null;
  private pipeline: GPUComputePipeline | null = null;
  private isFallback = false;
  private deviceLostCount = 0;
  private layerBuffers: Map<string, { inputBuffer: GPUBuffer; outputBuffer: GPUBuffer }> = new Map();

  public isSupported(): boolean {
    return (
      typeof navigator !== "undefined" &&
      "gpu" in navigator &&
      navigator.gpu !== undefined
    );
  }

  public async initialize(): Promise<boolean> {
    if (!this.isSupported()) {
      this.isFallback = true;
      return false;
    }

    try {
      const gpu = navigator.gpu;
      if (!gpu) {
        this.isFallback = true;
        return false;
      }
      this.adapter = await gpu.requestAdapter({ powerPreference: "high-performance" });
      if (!this.adapter) {
        this.isFallback = true;
        return false;
      }

      this.device = await this.adapter.requestDevice();

      this.device.lost.then((info: { reason: string }) => {
        console.warn(`WebGPU device lost: ${info.reason}. Switching to WebGL fallback.`);
        this.deviceLostCount++;
        this.isFallback = true;
        this.device = null;
        this.pipeline = null;
      });

      const shaderModule = this.device.createShaderModule({
        code: WGSL_COLOR_RAMP_SHADER,
      });

      this.pipeline = this.device.createComputePipeline({
        layout: "auto",
        compute: {
          module: shaderModule,
          entryPoint: "main",
        },
      });

      this.isFallback = false;
      return true;
    } catch {
      this.isFallback = true;
      this.device = null;
      return false;
    }
  }

  /**
   * Processes layer feature Float32Array on WebGPU (or zero-copy WebGL fallback).
   */
  public async processLayerBuffer(
    layerId: string,
    packedData: Float32Array,
    options: { minVal?: number; maxVal?: number; opacity?: number } = {}
  ): Promise<{ colors: Float32Array; isFallback: boolean }> {
    const count = Math.floor(packedData.length / 4);
    if (count === 0) {
      return { colors: new Float32Array(0), isFallback: this.isFallback };
    }

    if (this.isFallback || !this.device || !this.pipeline) {
      return { colors: this.processWebGLFallback(packedData, options), isFallback: true };
    }

    try {
      const minVal = options.minVal ?? 0;
      const maxVal = options.maxVal ?? 100;
      const opacity = options.opacity ?? 1.0;

      // 1. Create or retrieve GPU buffers
      const inputByteSize = packedData.byteLength;
      const outputByteSize = count * 4 * 4; // RGBA float32

      const inputBuffer = this.device.createBuffer({
        size: inputByteSize,
        usage: GPU_BUFFER_USAGE.STORAGE | GPU_BUFFER_USAGE.COPY_DST,
      });

      const outputBuffer = this.device.createBuffer({
        size: outputByteSize,
        usage: GPU_BUFFER_USAGE.STORAGE | GPU_BUFFER_USAGE.COPY_SRC,
      });

      const readbackBuffer = this.device.createBuffer({
        size: outputByteSize,
        usage: GPU_BUFFER_USAGE.MAP_READ | GPU_BUFFER_USAGE.COPY_DST,
      });

      const uniformArray = new Float32Array([minVal, maxVal, opacity, count]);
      const uniformBuffer = this.device.createBuffer({
        size: 16,
        usage: GPU_BUFFER_USAGE.UNIFORM | GPU_BUFFER_USAGE.COPY_DST,
      });

      this.device.queue.writeBuffer(inputBuffer, 0, packedData.buffer as ArrayBuffer, packedData.byteOffset, inputByteSize);
      this.device.queue.writeBuffer(uniformBuffer, 0, uniformArray.buffer);

      const bindGroup = this.device.createBindGroup({
        layout: this.pipeline.getBindGroupLayout(0),
        entries: [
          { binding: 0, resource: { buffer: inputBuffer } },
          { binding: 1, resource: { buffer: outputBuffer } },
          { binding: 2, resource: { buffer: uniformBuffer } },
        ],
      });

      const commandEncoder = this.device.createCommandEncoder();
      const pass = commandEncoder.beginComputePass();
      pass.setPipeline(this.pipeline);
      pass.setBindGroup(0, bindGroup);
      pass.dispatchWorkgroups(Math.ceil(count / 64));
      pass.end();

      commandEncoder.copyBufferToBuffer(outputBuffer, 0, readbackBuffer, 0, outputByteSize);
      this.device.queue.submit([commandEncoder.finish()]);

      await readbackBuffer.mapAsync(GPU_MAP_MODE.READ);
      const mappedRange = readbackBuffer.getMappedRange();
      const colors = new Float32Array(mappedRange.slice(0));
      readbackBuffer.unmap();

      inputBuffer.destroy();
      outputBuffer.destroy();
      readbackBuffer.destroy();
      uniformBuffer.destroy();

      return { colors, isFallback: false };
    } catch {
      this.isFallback = true;
      return { colors: this.processWebGLFallback(packedData, options), isFallback: true };
    }
  }

  /**
   * Zero-copy WebGL / CPU fallback renderer for older devices without WebGPU.
   */
  public processWebGLFallback(
    packedData: Float32Array,
    options: { minVal?: number; maxVal?: number; opacity?: number } = {}
  ): Float32Array {
    const count = Math.floor(packedData.length / 4);
    const colors = new Float32Array(count * 4);
    const minVal = options.minVal ?? 0;
    const maxVal = options.maxVal ?? 100;
    const opacity = options.opacity ?? 1.0;
    const range = Math.max(maxVal - minVal, 0.001);

    for (let i = 0; i < count; i++) {
      const val = packedData[i * 4 + 2];
      const norm = Math.min(1.0, Math.max(0.0, (val - minVal) / range));

      const r = Math.min(1.0, Math.max(0.0, 2.0 * norm - 0.5));
      const g = Math.min(1.0, Math.max(0.0, norm < 0.5 ? 2.0 * norm : 2.0 * (1.0 - norm)));
      const b = Math.min(1.0, Math.max(0.0, 1.0 - 2.0 * norm));

      const offset = i * 4;
      colors[offset] = r;
      colors[offset + 1] = g;
      colors[offset + 2] = b;
      colors[offset + 3] = opacity;
    }

    return colors;
  }

  public getState(): WebGPUAcceleratorState {
    return {
      isSupported: this.isSupported(),
      isInitialized: this.device !== null,
      isFallbackActive: this.isFallback,
      activeLayerCount: this.layerBuffers.size,
      deviceLostCount: this.deviceLostCount,
    };
  }
}

export const globalWebGPUAccelerator = new WebGPUAccelerator();
