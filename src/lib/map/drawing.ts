import type maplibregl from "maplibre-gl";
import { LngLat } from "maplibre-gl";

type Coord = [number, number];

// Ramer-Douglas-Peucker line simplification, on planar degrees.
function douglasPeucker(points: Coord[], epsilon: number): Coord[] {
  if (points.length <= 2) return points;
  let maxDistance = 0;
  let farthestIndex = 0;
  const start = points[0];
  const end = points[points.length - 1];
  const spanX = end[0] - start[0];
  const spanY = end[1] - start[1];
  const segmentLengthSquared = spanX * spanX + spanY * spanY;

  for (let index = 1; index < points.length - 1; index++) {
    let distance: number;
    if (segmentLengthSquared === 0) {
      const offsetX = points[index][0] - start[0];
      const offsetY = points[index][1] - start[1];
      distance = Math.sqrt(offsetX * offsetX + offsetY * offsetY);
    } else {
      const projection = Math.max(
        0,
        Math.min(
          1,
          ((points[index][0] - start[0]) * spanX +
            (points[index][1] - start[1]) * spanY) /
            segmentLengthSquared
        )
      );
      const perpendicularX = start[0] + projection * spanX - points[index][0];
      const perpendicularY = start[1] + projection * spanY - points[index][1];
      distance = Math.sqrt(
        perpendicularX * perpendicularX + perpendicularY * perpendicularY
      );
    }
    if (distance > maxDistance) {
      maxDistance = distance;
      farthestIndex = index;
    }
  }

  if (maxDistance > epsilon) {
    const before = douglasPeucker(points.slice(0, farthestIndex + 1), epsilon);
    const after = douglasPeucker(points.slice(farthestIndex), epsilon);
    return [...before.slice(0, -1), ...after];
  }
  return [start, end];
}

export function createPointHandler(
  map: maplibregl.Map,
  onAdd: (feature: GeoJSON.Feature) => void
): () => void {
  const click = (e: maplibregl.MapMouseEvent) => {
    const { lng, lat } = e.lngLat;
    const feature: GeoJSON.Feature = {
      type: "Feature",
      geometry: { type: "Point", coordinates: [lng, lat] },
      properties: {},
    };
    onAdd(feature);
  };

  map.on("click", click);
  map.getCanvas().style.cursor = "crosshair";

  return () => {
    map.off("click", click);
    map.getCanvas().style.cursor = "";
  };
}

export function createLineHandler(
  map: maplibregl.Map,
  onUpdate: (feature: GeoJSON.Feature) => void,
  onFinish: (feature: GeoJSON.Feature) => void
): () => void {
  const coords: Coord[] = [];
  let currentFeature: GeoJSON.Feature | null = null;

  const click = (e: maplibregl.MapMouseEvent) => {
    const { lng, lat } = e.lngLat;
    coords.push([lng, lat]);
    if (coords.length >= 2) {
      currentFeature = {
        type: "Feature",
        geometry: { type: "LineString", coordinates: [...coords] },
        properties: {},
      };
      onUpdate(currentFeature);
    }
  };

  const dblclick = (e: maplibregl.MapMouseEvent) => {
    e.preventDefault();
    if (coords.length >= 2) {
      const feature: GeoJSON.Feature = {
        type: "Feature",
        geometry: { type: "LineString", coordinates: [...coords] },
        properties: {},
      };
      onFinish(feature);
    }
    coords.length = 0;
    currentFeature = null;
  };

  map.on("click", click);
  map.on("dblclick", dblclick);
  map.getCanvas().style.cursor = "crosshair";

  return () => {
    map.off("click", click);
    map.off("dblclick", dblclick);
    map.getCanvas().style.cursor = "";
  };
}

export function createPolygonHandler(
  map: maplibregl.Map,
  onUpdate: (feature: GeoJSON.Feature) => void,
  onFinish: (feature: GeoJSON.Feature) => void
): () => void {
  const coords: Coord[] = [];
  const SNAP_THRESHOLD_PX = 10;

  const click = (e: maplibregl.MapMouseEvent) => {
    const { lng, lat } = e.lngLat;

    if (coords.length >= 3) {
      const first = map.project(
        new LngLat(coords[0][0], coords[0][1])
      );
      const click_px = map.project(e.lngLat);
      const dist = Math.sqrt(
        Math.pow(first.x - click_px.x, 2) + Math.pow(first.y - click_px.y, 2)
      );
      if (dist < SNAP_THRESHOLD_PX) {
        const ring = [...coords, coords[0]];
        const feature: GeoJSON.Feature = {
          type: "Feature",
          geometry: { type: "Polygon", coordinates: [ring] },
          properties: {},
        };
        onFinish(feature);
        coords.length = 0;
        return;
      }
    }

    coords.push([lng, lat]);

    if (coords.length >= 3) {
      const ring = [...coords, coords[0]];
      const feature: GeoJSON.Feature = {
        type: "Feature",
        geometry: { type: "Polygon", coordinates: [ring] },
        properties: {},
      };
      onUpdate(feature);
    }
  };

  const dblclick = (e: maplibregl.MapMouseEvent) => {
    e.preventDefault();
    if (coords.length >= 3) {
      const ring = [...coords, coords[0]];
      const feature: GeoJSON.Feature = {
        type: "Feature",
        geometry: { type: "Polygon", coordinates: [ring] },
        properties: {},
      };
      onFinish(feature);
    }
    coords.length = 0;
  };

  map.on("click", click);
  map.on("dblclick", dblclick);
  map.getCanvas().style.cursor = "crosshair";

  return () => {
    map.off("click", click);
    map.off("dblclick", dblclick);
    map.getCanvas().style.cursor = "";
  };
}

export function createRectangleHandler(
  map: maplibregl.Map,
  onUpdate: (feature: GeoJSON.Feature) => void,
  onFinish: (feature: GeoJSON.Feature) => void
): () => void {
  let startCoord: Coord | null = null;
  let isDragging = false;

  const bboxToPolygon = (a: Coord, b: Coord): GeoJSON.Feature => ({
    type: "Feature",
    geometry: {
      type: "Polygon",
      coordinates: [
        [
          [a[0], a[1]],
          [b[0], a[1]],
          [b[0], b[1]],
          [a[0], b[1]],
          [a[0], a[1]],
        ],
      ],
    },
    properties: {},
  });

  const mousedown = (e: maplibregl.MapMouseEvent) => {
    startCoord = [e.lngLat.lng, e.lngLat.lat];
    isDragging = true;
    map.dragPan.disable();
  };

  const mousemove = (e: maplibregl.MapMouseEvent) => {
    if (!isDragging || !startCoord) return;
    const end: Coord = [e.lngLat.lng, e.lngLat.lat];
    onUpdate(bboxToPolygon(startCoord, end));
  };

  const mouseup = (e: maplibregl.MapMouseEvent) => {
    if (!isDragging || !startCoord) return;
    isDragging = false;
    const end: Coord = [e.lngLat.lng, e.lngLat.lat];
    onFinish(bboxToPolygon(startCoord, end));
    startCoord = null;
    map.dragPan.enable();
  };

  map.on("mousedown", mousedown);
  map.on("mousemove", mousemove);
  map.on("mouseup", mouseup);
  map.getCanvas().style.cursor = "crosshair";

  return () => {
    map.off("mousedown", mousedown);
    map.off("mousemove", mousemove);
    map.off("mouseup", mouseup);
    map.dragPan.enable();
    map.getCanvas().style.cursor = "";
  };
}

export function createCircleHandler(
  map: maplibregl.Map,
  onUpdate: (feature: GeoJSON.Feature) => void,
  onFinish: (feature: GeoJSON.Feature) => void
): () => void {
  let center: Coord | null = null;
  let isDragging = false;
  const STEPS = 64;

  const circlePolygon = (cx: number, cy: number, radiusDeg: number): GeoJSON.Feature => {
    const coords: Coord[] = [];
    for (let i = 0; i <= STEPS; i++) {
      const angle = (i / STEPS) * 2 * Math.PI;
      coords.push([cx + radiusDeg * Math.cos(angle), cy + radiusDeg * Math.sin(angle)]);
    }
    return {
      type: "Feature",
      geometry: { type: "Polygon", coordinates: [coords] },
      properties: { center: [cx, cy], radiusDeg },
    };
  };

  const mousedown = (e: maplibregl.MapMouseEvent) => {
    center = [e.lngLat.lng, e.lngLat.lat];
    isDragging = true;
    map.dragPan.disable();
  };

  const mousemove = (e: maplibregl.MapMouseEvent) => {
    if (!isDragging || !center) return;
    const dx = e.lngLat.lng - center[0];
    const dy = e.lngLat.lat - center[1];
    const radius = Math.sqrt(dx * dx + dy * dy);
    onUpdate(circlePolygon(center[0], center[1], radius));
  };

  const mouseup = (e: maplibregl.MapMouseEvent) => {
    if (!isDragging || !center) return;
    isDragging = false;
    const dx = e.lngLat.lng - center[0];
    const dy = e.lngLat.lat - center[1];
    const radius = Math.sqrt(dx * dx + dy * dy);
    onFinish(circlePolygon(center[0], center[1], radius));
    center = null;
    map.dragPan.enable();
  };

  map.on("mousedown", mousedown);
  map.on("mousemove", mousemove);
  map.on("mouseup", mouseup);
  map.getCanvas().style.cursor = "crosshair";

  return () => {
    map.off("mousedown", mousedown);
    map.off("mousemove", mousemove);
    map.off("mouseup", mouseup);
    map.dragPan.enable();
    map.getCanvas().style.cursor = "";
  };
}

export function createFreehandHandler(
  map: maplibregl.Map,
  onUpdate: (feature: GeoJSON.Feature) => void,
  onFinish: (feature: GeoJSON.Feature) => void
): () => void {
  let points: Coord[] = [];
  let isDragging = false;
  const EPSILON = 0.0001;

  const mousedown = (e: maplibregl.MapMouseEvent) => {
    points = [[e.lngLat.lng, e.lngLat.lat]];
    isDragging = true;
    map.dragPan.disable();
  };

  const mousemove = (e: maplibregl.MapMouseEvent) => {
    if (!isDragging) return;
    points.push([e.lngLat.lng, e.lngLat.lat]);
    if (points.length >= 2) {
      onUpdate({
        type: "Feature",
        geometry: { type: "LineString", coordinates: [...points] },
        properties: {},
      });
    }
  };

  const mouseup = () => {
    if (!isDragging) return;
    isDragging = false;
    map.dragPan.enable();
    if (points.length >= 2) {
      const simplified = douglasPeucker(points, EPSILON);
      onFinish({
        type: "Feature",
        geometry: { type: "LineString", coordinates: simplified },
        properties: {},
      });
    }
    points = [];
  };

  map.on("mousedown", mousedown);
  map.on("mousemove", mousemove);
  map.on("mouseup", mouseup);
  map.getCanvas().style.cursor = "crosshair";

  return () => {
    map.off("mousedown", mousedown);
    map.off("mousemove", mousemove);
    map.off("mouseup", mouseup);
    map.dragPan.enable();
    map.getCanvas().style.cursor = "";
  };
}
