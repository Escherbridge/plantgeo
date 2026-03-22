"use client";

import { useEffect, useRef } from "react";
import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import type { CustomLayerInterface, CustomRenderMethodInput } from "maplibre-gl";
import type maplibregl from "maplibre-gl";
import { useMap } from "@/lib/map/map-context";
import { MODEL_LIBRARY } from "@/lib/map/model-library";

interface ModelLayerProps {
  id?: string;
  coordinates: [number, number];
  modelType: string;
  scale?: number;
  rotation?: number;
}

const MERCATOR_A = 6378137.0;
const MERCATOR_MAX = Math.PI * MERCATOR_A;

function mercatorXY(lng: number, lat: number): [number, number] {
  const x = (lng / 180) * MERCATOR_MAX;
  const y = Math.log(Math.tan(((90 + lat) * Math.PI) / 360)) * MERCATOR_A;
  return [x, y];
}

function buildModelLayer(
  id: string,
  coordinates: [number, number],
  modelType: string,
  scale: number,
  rotation: number
): CustomLayerInterface {
  let scene: THREE.Scene;
  let camera: THREE.Camera;
  let renderer: THREE.WebGLRenderer;
  let currentMap: maplibregl.Map | null = null;

  const modelDef = MODEL_LIBRARY[modelType];

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
      dirLight.position.set(0, -70, 100).normalize();
      scene.add(dirLight);

      renderer = new THREE.WebGLRenderer({
        canvas: m.getCanvas(),
        context: gl as WebGL2RenderingContext,
        antialias: true,
      });
      renderer.autoClear = false;

      const loader = new GLTFLoader();
      const url = modelDef?.url ?? `/models/${modelType}.glb`;
      const defaultScale = modelDef?.defaultScale ?? 1;
      const [defaultRx, defaultRy, defaultRz] = modelDef?.defaultRotation ?? [0, 0, 0];

      const [lng, lat] = coordinates;
      const [x, y] = mercatorXY(lng, lat);

      loader.load(url, (gltf) => {
        const model = gltf.scene;
        model.scale.set(
          defaultScale * scale,
          defaultScale * scale,
          defaultScale * scale
        );
        model.rotation.set(defaultRx, defaultRy + rotation, defaultRz);
        model.position.set(x, 0, -y);

        const meshes: THREE.Mesh[] = [];
        model.traverse((child) => {
          if ((child as THREE.Mesh).isMesh) meshes.push(child as THREE.Mesh);
        });

        if (meshes.length > 10) {
          const dummy = new THREE.Object3D();
          const firstMesh = meshes[0];
          const geo = (firstMesh as THREE.Mesh).geometry;
          const mat = (firstMesh as THREE.Mesh).material as THREE.Material;
          const instanced = new THREE.InstancedMesh(geo, mat, meshes.length);
          meshes.forEach((mesh, i) => {
            dummy.position.copy(mesh.getWorldPosition(new THREE.Vector3()));
            dummy.quaternion.copy(mesh.getWorldQuaternion(new THREE.Quaternion()));
            dummy.scale.copy(mesh.getWorldScale(new THREE.Vector3()));
            dummy.updateMatrix();
            instanced.setMatrixAt(i, dummy.matrix);
          });
          instanced.instanceMatrix.needsUpdate = true;
          instanced.position.set(x, 0, -y);
          scene.add(instanced);
        } else {
          scene.add(model);
        }

        if (currentMap) currentMap.triggerRepaint();
      });
    },

    render(_gl: WebGLRenderingContext | WebGL2RenderingContext, options: CustomRenderMethodInput) {
      const matrix = new THREE.Matrix4().fromArray(
        options.modelViewProjectionMatrix as unknown as number[]
      );
      camera.projectionMatrix = matrix;
      camera.projectionMatrixInverse = matrix.clone().invert();

      renderer.resetState();
      renderer.render(scene, camera);

      if (currentMap) currentMap.triggerRepaint();
    },

    onRemove() {
      renderer.dispose();
      currentMap = null;
    },
  };

  return layer;
}

export default function ModelLayer({
  id = "model-layer",
  coordinates,
  modelType,
  scale = 1,
  rotation = 0,
}: ModelLayerProps) {
  const map = useMap();
  const layerRef = useRef<CustomLayerInterface | null>(null);

  useEffect(() => {
    if (!map) return;

    const layer = buildModelLayer(id, coordinates, modelType, scale, rotation);
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
