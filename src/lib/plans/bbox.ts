import { clampCameraZoom } from "@/lib/map/camera-limits";
import { viewportForBbox } from "@/lib/map/coverage-region";

export type PlanBbox = {
  west: number;
  south: number;
  east: number;
  north: number;
};

/** A stored camera box. Rejects anything that is not a finite, ordered WGS84 span. */
export function isPlanBbox(value: unknown): value is PlanBbox {
  if (typeof value !== "object" || value === null) return false;
  const record = value as Record<string, unknown>;
  const west = record.west;
  const south = record.south;
  const east = record.east;
  const north = record.north;
  if (
    typeof west !== "number" ||
    typeof south !== "number" ||
    typeof east !== "number" ||
    typeof north !== "number"
  ) {
    return false;
  }
  if (![west, south, east, north].every((corner) => Number.isFinite(corner))) return false;
  if (west < -180 || east > 180 || south < -90 || north > 90) return false;
  return west < east && south < north;
}

const PLAN_CAMERA_WIDTH_PX = 1280;
const PLAN_CAMERA_HEIGHT_PX = 800;
const PLAN_CAMERA_PADDING_PX = 48;

/** The opening camera for a stored plan box, clamped to the map's own zoom ceiling. */
export function cameraForPlanBbox(bbox: PlanBbox): {
  longitude: number;
  latitude: number;
  zoom: number;
} {
  const camera = viewportForBbox(
    bbox,
    PLAN_CAMERA_WIDTH_PX,
    PLAN_CAMERA_HEIGHT_PX,
    PLAN_CAMERA_PADDING_PX
  );
  return {
    longitude: camera.longitude,
    latitude: camera.latitude,
    zoom: clampCameraZoom(camera.zoom),
  };
}
