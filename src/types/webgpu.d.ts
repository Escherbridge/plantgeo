/* Ambient WebGPU type definitions for browsers without dom.webgpu lib */

interface GPUComputePipeline {
  getBindGroupLayout(index: number): unknown;
}

interface GPUAdapter {
  requestDevice(descriptor?: Record<string, unknown>): Promise<GPUDevice>;
}

interface GPUDevice {
  lost: Promise<{ reason: string }>;
  createShaderModule(descriptor: { code: string }): unknown;
  createComputePipeline(descriptor: unknown): GPUComputePipeline;
  createBuffer(descriptor: { size: number; usage: number }): GPUBuffer;
  createBindGroup(descriptor: unknown): unknown;
  createCommandEncoder(): {
    beginComputePass(): {
      setPipeline(pipeline: unknown): void;
      setBindGroup(index: number, bindGroup: unknown): void;
      dispatchWorkgroups(workgroupsX: number): void;
      end(): void;
    };
    copyBufferToBuffer(
      source: GPUBuffer,
      sourceOffset: number,
      destination: GPUBuffer,
      destinationOffset: number,
      size: number
    ): void;
    finish(): unknown;
  };
  queue: {
    writeBuffer(
      buffer: GPUBuffer,
      bufferOffset: number,
      data: ArrayBuffer | ArrayBufferView,
      dataOffset?: number,
      size?: number
    ): void;
    submit(commandBuffers: unknown[]): void;
  };
}

interface GPUBuffer {
  destroy(): void;
  mapAsync(mode: number): Promise<void>;
  getMappedRange(offset?: number, size?: number): ArrayBuffer;
  unmap(): void;
}

declare const GPUBufferUsage: {
  STORAGE: number;
  VERTEX: number;
  COPY_DST: number;
  COPY_SRC: number;
  UNIFORM: number;
  MAP_READ: number;
};

declare const GPUMapMode: {
  READ: number;
  WRITE: number;
};

interface Navigator {
  gpu?: {
    requestAdapter(options?: Record<string, unknown>): Promise<GPUAdapter | null>;
  };
}
