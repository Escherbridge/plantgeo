"use client";

import { useEffect, useRef } from "react";
import * as THREE from "three";
import type { CustomLayerInterface, CustomRenderMethodInput } from "maplibre-gl";
import type maplibregl from "maplibre-gl";
import { useMap } from "@/lib/map/map-context";

interface AnimatedBeaconProps {
  id?: string;
  coordinates: [number, number];
  color?: number;
  maxRadius?: number;
}

const MERCATOR_A = 6378137.0;
const MERCATOR_MAX = Math.PI * MERCATOR_A;

function mercatorXY(lng: number, lat: number): [number, number] {
  const x = (lng / 180) * MERCATOR_MAX;
  const y = Math.log(Math.tan(((90 + lat) * Math.PI) / 360)) * MERCATOR_A;
  return [x, y];
}

function buildBeaconLayer(
  id: string,
  coordinates: [number, number],
  color: number,
  maxRadius: number
): CustomLayerInterface {
  let scene: THREE.Scene;
  let camera: THREE.Camera;
  let renderer: THREE.WebGLRenderer;
  let currentMap: maplibregl.Map | null = null;
  let animFrameId: number | null = null;
  let beaconMesh: THREE.Mesh | null = null;
  let startTime: number | null = null;

  const [lng, lat] = coordinates;
  const [posX, posY] = mercatorXY(lng, lat);

  function animate(timestamp: number) {
    if (startTime === null) startTime = timestamp;
    const elapsed = (timestamp - startTime) / 1000;
    const phase = (elapsed % 2) / 2;

    if (beaconMesh) {
      const s = phase * maxRadius;
      beaconMesh.scale.set(s, s, s);
      const mat = beaconMesh.material as THREE.MeshBasicMaterial;
      mat.opacity = 1 - phase;
    }

    if (currentMap) currentMap.triggerRepaint();
    animFrameId = requestAnimationFrame(animate);
  }

  const layer: CustomLayerInterface = {
    id,
    type: "custom" as const,
    renderingMode: "3d" as const,

    onAdd(m, gl) {
      currentMap = m;
      scene = new THREE.Scene();
      camera = new THREE.Camera();

      renderer = new THREE.WebGLRenderer({
        canvas: m.getCanvas(),
        context: gl as WebGL2RenderingContext,
        antialias: true,
      });
      renderer.autoClear = false;

      const geo = new THREE.SphereGeometry(1, 16, 16);
      const mat = new THREE.MeshBasicMaterial({
        color,
        transparent: true,
        opacity: 0.8,
        depthWrite: false,
      });
      beaconMesh = new THREE.Mesh(geo, mat);
      beaconMesh.position.set(posX, 0, -posY);
      scene.add(beaconMesh);

      animFrameId = requestAnimationFrame(animate);
    },

    render(_gl: WebGLRenderingContext | WebGL2RenderingContext, options: CustomRenderMethodInput) {
      const matrix = new THREE.Matrix4().fromArray(
        options.modelViewProjectionMatrix as unknown as number[]
      );
      camera.projectionMatrix = matrix;
      camera.projectionMatrixInverse = matrix.clone().invert();

      renderer.resetState();
      renderer.render(scene, camera);
    },

    onRemove() {
      if (animFrameId !== null) {
        cancelAnimationFrame(animFrameId);
        animFrameId = null;
      }
      renderer.dispose();
      currentMap = null;
    },
  };

  return layer;
}

export default function AnimatedBeacon({
  id = "animated-beacon",
  coordinates,
  color = 0xff4500,
  maxRadius = 50,
}: AnimatedBeaconProps) {
  const map = useMap();
  const layerRef = useRef<CustomLayerInterface | null>(null);

  useEffect(() => {
    if (!map) return;

    const layer = buildBeaconLayer(id, coordinates, color, maxRadius);
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
