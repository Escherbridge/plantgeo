export interface ModelDefinition {
  name: string;
  url: string;
  defaultScale: number;
  defaultRotation: [number, number, number];
}

export const MODEL_LIBRARY: Record<string, ModelDefinition> = {
  wind_turbine: {
    name: "Wind Turbine",
    url: "/models/wind_turbine.glb",
    defaultScale: 1,
    defaultRotation: [0, 0, 0],
  },
  fire_station: {
    name: "Fire Station",
    url: "/models/fire_station.glb",
    defaultScale: 1,
    defaultRotation: [0, 0, 0],
  },
  sensor_tower: {
    name: "Sensor Tower",
    url: "/models/sensor_tower.glb",
    defaultScale: 1,
    defaultRotation: [0, 0, 0],
  },
  vehicle: {
    name: "Vehicle",
    url: "/models/vehicle.glb",
    defaultScale: 1,
    defaultRotation: [0, 0, 0],
  },
  tree: {
    name: "Tree",
    url: "/models/tree.glb",
    defaultScale: 1,
    defaultRotation: [0, 0, 0],
  },
};
