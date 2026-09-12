import type { CustomLayerInterface, CustomRenderMethodInput, GeoJSONSource, Map as MapLibreMap, MapSourceDataEvent } from "maplibre-gl";
import { buildScalarFieldMesh, scalarFieldCellSpacing, type ScalarFieldMesh } from "./scalar-field";

interface ScalarFieldOptions {
  id: string;
  nativeFillId: string;
  nativeOutlineId: string;
  nativeSourceId: string;
  labelId: string;
  valueProperty: string;
  range: readonly [number, number];
  ramp: readonly { value: number; color: string }[];
}

/** Exact nearest-cell field with native inspection/failure fallback; see AGENTS.md. */
export class ScalarFieldLayer implements CustomLayerInterface {
  readonly type = "custom" as const;
  readonly renderingMode = "2d" as const;
  readonly id: string;
  private map: MapLibreMap | null = null;
  private gl: WebGL2RenderingContext | null = null;
  private program: WebGLProgram | null = null;
  private buffer: WebGLBuffer | null = null;
  private vao: WebGLVertexArrayObject | null = null;
  private mesh: ScalarFieldMesh | null = null;
  private spacingCells: ScalarFieldMesh["cells"] = [];
  private collection: GeoJSON.FeatureCollection | null = null;
  private opacity = 0;
  private visible = false;
  private active = false;
  private failed = false;
  private ready = false;
  private nativeOpacity: number | undefined;
  private source: GeoJSONSource | null = null;
  private layoutPending = false;
  private dataPending = false;

  constructor(private readonly options: ScalarFieldOptions) {
    this.id = options.id;
  }

  update(collection: GeoJSON.FeatureCollection | null, opacity: number, visible: boolean): void {
    this.opacity = Math.max(0, Math.min(1, opacity));
    this.visible = visible;
    if (this.collection !== collection) {
      this.collection = collection;
      this.dataPending = this.map !== null;
      this.ready = false;
      this.mesh = buildScalarFieldMesh(collection, this.options.valueProperty, this.options.range);
      const cells = this.mesh?.cells ?? [];
      this.spacingCells = cells.length ? [
        cells.reduce((a, b) => a.north > b.north ? a : b),
        cells.reduce((a, b) => a.south < b.south ? a : b),
      ] : [];
      if (this.gl && this.buffer && !this.failed) this.upload();
    }
    // The native owner also writes paint during prop updates.
    this.nativeOpacity = undefined;
    this.flushSource();
    this.sync();
    this.map?.triggerRepaint();
  }

  onAdd(map: MapLibreMap, context: WebGLRenderingContext | WebGL2RenderingContext): void {
    this.map = map;
    this.source = map.getSource(this.options.nativeSourceId) as GeoJSONSource;
    map.on("move", this.sync);
    map.on("styledata", this.sync);
    map.on("sourcedata", this.sourceReady);
    map.on("idle", this.sourceSettled);
    map.on("webglcontextlost", this.contextLost);
    this.failed = true;
    if (typeof WebGL2RenderingContext !== "undefined" && context instanceof WebGL2RenderingContext) {
      this.gl = context;
      try {
        this.initialize(context);
        this.failed = false;
        this.upload();
      } catch {
        this.failed = true;
      }
    }
    this.sync();
    // Repaint after style reattachment so the custom layer can become ready.
    map.triggerRepaint();
  }

  private initialize(gl: WebGL2RenderingContext): void {
    const previousProgram = gl.getParameter(gl.CURRENT_PROGRAM) as WebGLProgram | null;
    const previousBuffer = gl.getParameter(gl.ARRAY_BUFFER_BINDING) as WebGLBuffer | null;
    const previousVao = gl.getParameter(gl.VERTEX_ARRAY_BINDING) as WebGLVertexArrayObject | null;
    const compile = (type: number, source: string) => {
      const shader = gl.createShader(type);
      if (!shader) throw new Error("scalar shader allocation");
      gl.shaderSource(shader, source);
      gl.compileShader(shader);
      if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
        gl.deleteShader(shader);
        throw new Error("scalar shader compilation");
      }
      return shader;
    };
    const vertex = compile(gl.VERTEX_SHADER, `#version 300 es
      in vec3 a_cell;
      uniform mat4 u_matrix;
      uniform float u_world;
      flat out float v_value;
      void main() {
        v_value = a_cell.z;
        gl_Position = u_matrix * vec4(a_cell.x + u_world, a_cell.y, 0.0, 1.0);
      }`);
    let fragment: WebGLShader | undefined;
    try {
      const ramp = this.options.ramp;
      fragment = compile(gl.FRAGMENT_SHADER, `#version 300 es
        precision highp float;
        flat in float v_value;
        uniform float u_opacity;
        uniform float u_stops[${ramp.length}];
        uniform vec3 u_colors[${ramp.length}];
        out vec4 color;
        void main() {
          vec3 rgb = u_colors[0];
          for (int i = 1; i < ${ramp.length}; i++) {
            float t = clamp((v_value - u_stops[i-1]) / (u_stops[i] - u_stops[i-1]), 0.0, 1.0);
            rgb = mix(rgb, u_colors[i], t);
          }
          color = vec4(rgb * u_opacity, u_opacity);
        }`);
      this.program = gl.createProgram();
      if (!this.program) throw new Error("scalar program allocation");
      gl.attachShader(this.program, vertex);
      gl.attachShader(this.program, fragment);
      gl.linkProgram(this.program);
      if (!gl.getProgramParameter(this.program, gl.LINK_STATUS)) throw new Error("scalar program link");
      this.buffer = gl.createBuffer();
      this.vao = gl.createVertexArray();
      if (!this.buffer || !this.vao) throw new Error("scalar buffer allocation");
      gl.bindVertexArray(this.vao);
      gl.bindBuffer(gl.ARRAY_BUFFER, this.buffer);
      const attribute = gl.getAttribLocation(this.program, "a_cell");
      gl.enableVertexAttribArray(attribute);
      gl.vertexAttribPointer(attribute, 3, gl.FLOAT, false, 12, 0);
      gl.bindVertexArray(null);
      gl.useProgram(this.program);
      gl.uniform1fv(gl.getUniformLocation(this.program, "u_stops"), ramp.map((stop) => stop.value));
      gl.uniform3fv(gl.getUniformLocation(this.program, "u_colors"), ramp.flatMap((stop) => [1, 3, 5].map((start) => parseInt(stop.color.slice(start, start + 2), 16) / 255)));
      if (gl.getError() !== gl.NO_ERROR) throw new Error("scalar initialization");
    } finally {
      gl.deleteShader(vertex);
      if (fragment) gl.deleteShader(fragment);
      gl.bindVertexArray(previousVao);
      gl.bindBuffer(gl.ARRAY_BUFFER, previousBuffer);
      gl.useProgram(previousProgram);
    }
  }

  private upload(): void {
    const gl = this.gl;
    if (!gl || !this.buffer) return;
    const previousBuffer = gl.getParameter(gl.ARRAY_BUFFER_BINDING) as WebGLBuffer | null;
    try {
      gl.bindBuffer(gl.ARRAY_BUFFER, this.buffer);
      gl.bufferData(gl.ARRAY_BUFFER, this.mesh?.vertices ?? new Float32Array(), gl.STATIC_DRAW);
      if (gl.getError() !== gl.NO_ERROR) throw new Error("scalar upload");
    } catch {
      this.failed = true;
    } finally {
      gl.bindBuffer(gl.ARRAY_BUFFER, previousBuffer);
    }
  }

  private contextLost = (): void => {
    this.failed = true;
    this.sync();
  };

  private sourceReady = (event: MapSourceDataEvent): void => {
    if (event.sourceId === this.options.nativeSourceId && event.sourceDataType !== "metadata" && !this.layoutPending) this.sourceSettled();
  };

  private sourceSettled = (): void => {
    const map = this.map;
    if (!map || map.getSource(this.options.nativeSourceId) !== this.source || !map.isSourceLoaded(this.options.nativeSourceId)) return;
    this.layoutPending = false;
    this.flushSource();
    this.sync();
  };

  private flushSource(): void {
    if (!this.dataPending || this.layoutPending || !this.map || !this.source || this.map.getSource(this.options.nativeSourceId) !== this.source) return;
    try {
      this.source.setData(this.collection ?? { type: "FeatureCollection", features: [] });
      this.dataPending = false;
    } catch {
      this.active = false;
    }
  }

  private sync = (): void => {
    const map = this.map;
    if (!map) return;
    try {
      const mercator = map.getProjection()?.type === "mercator";
      const bounds = map.getBounds();
      const worldCount = Math.floor((bounds.getEast() + 180) / 360) - Math.floor((bounds.getWest() + 180) / 360) + 1;
      const supported = mercator && !map.getTerrain() && map.getPitch() === 0 && worldCount <= 16;
      const spacing = this.mesh ? scalarFieldCellSpacing(this.spacingCells, (point) => map.project(point)) : 0;
      const inspect = spacing >= 64;
      const sourceLoaded = map.getSource(this.options.nativeSourceId) === this.source && map.isSourceLoaded(this.options.nativeSourceId);
      this.active = this.visible && supported && !this.failed && !!this.mesh && !inspect && !this.dataPending && sourceLoaded;
      const nativeOpacity = !this.visible || (this.active && this.ready) ? 0 : this.opacity;
      const labelVisibility = this.visible && inspect ? "visible" : "none";
      if (nativeOpacity !== this.nativeOpacity && map.getLayer(this.options.nativeFillId)) {
        this.nativeOpacity = nativeOpacity;
        map.setPaintProperty(this.options.nativeFillId, "fill-opacity", nativeOpacity);
      }
      if (sourceLoaded && !this.dataPending && !this.layoutPending) {
        const nativeVisibility = this.visible ? "visible" : "none";
        const changes = [
          [this.options.nativeFillId, nativeVisibility],
          [this.options.nativeOutlineId, nativeVisibility],
          [this.options.labelId, labelVisibility],
        ].filter(([id, visibility]) => map.getLayer(id) && map.getLayoutProperty(id, "visibility") !== visibility);
        if (changes.length) this.layoutPending = true;
        for (const [id, visibility] of changes) map.setLayoutProperty(id, "visibility", visibility);
      }
      if (this.active && !this.ready) map.triggerRepaint();
    } catch {
      this.active = false;
      this.nativeOpacity = undefined;
      try {
        if (map.getLayer(this.options.nativeFillId)) map.setPaintProperty(this.options.nativeFillId, "fill-opacity", this.visible ? this.opacity : 0);
      } catch { /* The next style load rebuilds native layers. */ }
    }
  };

  render(context: WebGLRenderingContext | WebGL2RenderingContext, input: CustomRenderMethodInput): void {
    if (!this.active || !this.mesh || !this.program || !this.vao || context !== this.gl) return;
    const gl = this.gl;
    const map = this.map;
    if (!gl || !map) return;
    try {
      // Recheck on the frame as terrain/projection can change after the move event.
      if (map.getProjection()?.type !== "mercator" || map.getTerrain() || map.getPitch() !== 0 || gl.isContextLost()) {
        this.sync();
        return;
      }
      gl.useProgram(this.program);
      gl.bindVertexArray(this.vao);
      gl.uniformMatrix4fv(gl.getUniformLocation(this.program, "u_matrix"), false, input.defaultProjectionData.mainMatrix);
      gl.uniform1f(gl.getUniformLocation(this.program, "u_opacity"), this.ready ? this.opacity : 0);
      const bounds = map.getBounds();
      const firstWorld = Math.floor((bounds.getWest() + 180) / 360);
      const lastWorld = Math.floor((bounds.getEast() + 180) / 360);
      const worlds = map.getRenderWorldCopies() ? Array.from({ length: lastWorld - firstWorld + 1 }, (_, i) => firstWorld + i) : [0];
      for (const offset of worlds) {
        gl.uniform1f(gl.getUniformLocation(this.program, "u_world"), offset);
        gl.drawArrays(gl.TRIANGLES, 0, this.mesh.vertices.length / 3);
      }
      if (gl.getError() !== gl.NO_ERROR) throw new Error("scalar draw");
      if (!this.ready) {
        this.ready = true;
        this.sync();
        map.triggerRepaint();
      }
    } catch {
      this.failed = true;
      this.sync();
    } finally {
      gl.bindVertexArray(null);
    }
  }

  onRemove(): void {
    try {
      if (this.map?.getLayer(this.options.nativeFillId)) this.map.setPaintProperty(this.options.nativeFillId, "fill-opacity", this.visible ? this.opacity : 0);
    } catch { /* A replaced style recreates its own native layers. */ }
    this.map?.off("move", this.sync);
    this.map?.off("styledata", this.sync);
    this.map?.off("sourcedata", this.sourceReady);
    this.map?.off("idle", this.sourceSettled);
    this.map?.off("webglcontextlost", this.contextLost);
    if (this.gl) {
      if (this.program && this.gl.getParameter(this.gl.CURRENT_PROGRAM) === this.program) this.gl.useProgram(null);
      if (this.vao && this.gl.getParameter(this.gl.VERTEX_ARRAY_BINDING) === this.vao) this.gl.bindVertexArray(null);
      if (this.buffer && this.gl.getParameter(this.gl.ARRAY_BUFFER_BINDING) === this.buffer) this.gl.bindBuffer(this.gl.ARRAY_BUFFER, null);
      if (this.buffer) this.gl.deleteBuffer(this.buffer);
      if (this.vao) this.gl.deleteVertexArray(this.vao);
      if (this.program) this.gl.deleteProgram(this.program);
    }
    this.active = false;
    this.dataPending = false;
    this.layoutPending = false;
    this.source = null;
    this.map = null;
  }
}
