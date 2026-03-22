"use client";

import { useEffect, useRef } from "react";
import * as THREE from "three";
import type { CustomLayerInterface, CustomRenderMethodInput } from "maplibre-gl";
import type maplibregl from "maplibre-gl";
import { useMap } from "@/lib/map/map-context";

export interface BarConfig {
  coordinates: [number, number];
  height: number;
  color: number;
}

export interface ExtrudedPolygonConfig {
  geoJsonPolygon: GeoJSON.Polygon;
  height: number;
  color: number;
}

export interface ThreeLayerHandle {
  scene: THREE.Scene;
  createBar: (config: BarConfig) => THREE.Mesh;
  createExtrudedPolygon: (config: ExtrudedPolygonConfig) => THREE.Mesh;
}

interface ThreeLayerProps {
  id?: string;
  onReady?: (handle: ThreeLayerHandle) => void;
}

const MERCATOR_A = 6378137.0;
const MERCATOR_MAX = Math.PI * MERCATOR_A;

function mercatorXY(lng: number, lat: number): [number, number] {
  const x = (lng / 180) * MERCATOR_MAX;
  const y = Math.log(Math.tan(((90 + lat) * Math.PI) / 360)) * MERCATOR_A;
  return [x, y];
}

function buildCustomLayer(
  id: string,
  onReady?: (handle: ThreeLayerHandle) => void
): CustomLayerInterface {
  let scene: THREE.Scene;
  let camera: THREE.Camera;
  let renderer: THREE.WebGLRenderer;
  let currentMap: maplibregl.Map | null = null;
  let hoveredMesh: THREE.Mesh | null = null;

  const raycaster = new THREE.Raycaster();
  const mouse = new THREE.Vector2();

  function screenToNDC(
    canvas: HTMLCanvasElement,
    clientX: number,
    clientY: number
  ): THREE.Vector2 {
    const rect = canvas.getBoundingClientRect();
    return new THREE.Vector2(
      ((clientX - rect.left) / rect.width) * 2 - 1,
      -((clientY - rect.top) / rect.height) * 2 + 1
    );
  }

  function collectMeshes(): THREE.Mesh[] {
    const meshes: THREE.Mesh[] = [];
    scene.traverse((obj) => {
      if ((obj as THREE.Mesh).isMesh) meshes.push(obj as THREE.Mesh);
    });
    return meshes;
  }

  function onMouseMove(e: MouseEvent) {
    if (!renderer || !scene || !camera) return;
    mouse.copy(screenToNDC(renderer.domElement, e.clientX, e.clientY));
    raycaster.setFromCamera(mouse, camera);
    const intersects = raycaster.intersectObjects(collectMeshes(), false);

    if (hoveredMesh) {
      const mat = hoveredMesh.material as THREE.MeshPhongMaterial;
      if (mat?.emissive) mat.emissive.set(0x000000);
      hoveredMesh = null;
    }

    if (intersects.length > 0) {
      const mesh = intersects[0].object as THREE.Mesh;
      hoveredMesh = mesh;
      const mat = mesh.material as THREE.MeshPhongMaterial;
      if (mat?.emissive) mat.emissive.set(0x444444);
    }
  }

  function onClick(e: MouseEvent) {
    if (!renderer || !scene || !camera) return;
    const ndc = screenToNDC(renderer.domElement, e.clientX, e.clientY);
    raycaster.setFromCamera(ndc, camera);
    const intersects = raycaster.intersectObjects(collectMeshes(), false);

    if (intersects.length > 0) {
      const mesh = intersects[0].object as THREE.Mesh;
      renderer.domElement.dispatchEvent(
        new CustomEvent("three-object-click", {
          detail: { userData: mesh.userData },
          bubbles: true,
        })
      );
    }
  }

  function createBar({ coordinates, height, color }: BarConfig): THREE.Mesh {
    const [lng, lat] = coordinates;
    const [x, y] = mercatorXY(lng, lat);
    const geo = new THREE.BoxGeometry(10, height, 10);
    const mat = new THREE.MeshPhongMaterial({ color });
    const mesh = new THREE.Mesh(geo, mat);
    mesh.position.set(x, height / 2, -y);
    scene.add(mesh);
    return mesh;
  }

  function createExtrudedPolygon({
    geoJsonPolygon,
    height,
    color,
  }: ExtrudedPolygonConfig): THREE.Mesh {
    const ring = geoJsonPolygon.coordinates[0];
    const shape = new THREE.Shape();
    const [firstLng, firstLat] = ring[0];
    const [fx, fy] = mercatorXY(firstLng, firstLat);
    shape.moveTo(fx, -fy);
    for (let i = 1; i < ring.length; i++) {
      const [lng, lat] = ring[i];
      const [px, py] = mercatorXY(lng, lat);
      shape.lineTo(px, -py);
    }
    shape.closePath();

    const extrudeSettings = { depth: height, bevelEnabled: false };
    const geo = new THREE.ExtrudeGeometry(shape, extrudeSettings);
    const mat = new THREE.MeshPhongMaterial({ color });
    const mesh = new THREE.Mesh(geo, mat);
    scene.add(mesh);
    return mesh;
  }

  const layer: CustomLayerInterface = {
    id,
    type: "custom" as const,
    renderingMode: "3d" as const,

    onAdd(m, gl) {
      currentMap = m;
      scene = new THREE.Scene();
      camera = new THREE.Camera();

      const ambientLight = new THREE.AmbientLight(0xffffff, 0.6);
      scene.add(ambientLight);
      const dirLight = new THREE.DirectionalLight(0xffffff, 0.8);
      dirLight.position.set(1, 1, 1).normalize();
      scene.add(dirLight);

      renderer = new THREE.WebGLRenderer({
        canvas: m.getCanvas(),
        context: gl as WebGL2RenderingContext,
        antialias: true,
      });
      renderer.autoClear = false;

      const canvas = m.getCanvas();
      canvas.addEventListener("mousemove", onMouseMove);
      canvas.addEventListener("click", onClick);

      if (onReady) {
        onReady({ scene, createBar, createExtrudedPolygon });
      }
    },

    render(_gl: WebGLRenderingContext | WebGL2RenderingContext, options: CustomRenderMethodInput) {
      const matrix = new THREE.Matrix4().fromArray(
        options.modelViewProjectionMatrix as unknown as number[]
      );
      camera.projectionMatrix = matrix;
      camera.projectionMatrixInverse = matrix.clone().invert();

      const frustum = new THREE.Frustum();
      const projScreenMatrix = new THREE.Matrix4();
      projScreenMatrix.multiplyMatrices(
        camera.projectionMatrix,
        camera.matrixWorldInverse
      );
      frustum.setFromProjectionMatrix(projScreenMatrix);

      scene.traverse((obj) => {
        if ((obj as THREE.Mesh).isMesh) {
          const mesh = obj as THREE.Mesh;
          mesh.visible = frustum.intersectsObject(mesh);
        }
      });

      renderer.resetState();
      renderer.render(scene, camera);

      if (currentMap) currentMap.triggerRepaint();
    },

    onRemove() {
      if (currentMap) {
        const canvas = currentMap.getCanvas();
        canvas.removeEventListener("mousemove", onMouseMove);
        canvas.removeEventListener("click", onClick);
      }
      renderer.dispose();
      currentMap = null;
    },
  };

  return layer;
}

export default function ThreeLayer({ id = "three-layer", onReady }: ThreeLayerProps) {
  const map = useMap();
  const layerRef = useRef<CustomLayerInterface | null>(null);

  useEffect(() => {
    if (!map) return;

    const layer = buildCustomLayer(id, onReady);
    layerRef.current = layer;

    const addLayer = () => {
      if (!map.getLayer(id)) {
        map.addLayer(layer);
      }
    };

    if (map.isStyleLoaded()) {
      addLayer();
    } else {
      map.once("load", addLayer);
    }

    return () => {
      if (map.getLayer(id)) {
        map.removeLayer(id);
      }
      layerRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, id]);

  return null;
}
