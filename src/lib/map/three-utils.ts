import * as THREE from "three";
import type maplibregl from "maplibre-gl";

const MERCATOR_A = 6378137.0;
const MERCATOR_MAX = Math.PI * MERCATOR_A;

export function lngLatToMercator(
  lng: number,
  lat: number,
  altitude = 0
): THREE.Vector3 {
  const x = (lng / 180) * MERCATOR_MAX;
  const y =
    Math.log(Math.tan(((90 + lat) * Math.PI) / 360)) * MERCATOR_A;
  return new THREE.Vector3(x, altitude, -y);
}

export function getMercatorMatrix(map: maplibregl.Map): THREE.Matrix4 {
  const m = map as maplibregl.Map & {
    transform?: { mercatorMatrix?: number[] };
  };
  const raw = m.transform?.mercatorMatrix;
  if (!raw) return new THREE.Matrix4();
  return new THREE.Matrix4().fromArray(raw);
}

export function synchronizeCamera(
  camera: THREE.Camera,
  map: maplibregl.Map
): void {
  const matrix = getMercatorMatrix(map);
  camera.projectionMatrix = matrix;
  camera.projectionMatrixInverse = matrix.clone().invert();
}
