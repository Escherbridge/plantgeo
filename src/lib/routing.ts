export interface Maneuver {
  type: number;
  instruction: string;
  distance: number;
  time: number;
  beginShapeIndex: number;
  endShapeIndex: number;
}

export interface DecodedRoute {
  geometry: GeoJSON.LineString;
  maneuvers: Maneuver[];
  summary: {
    length: number;
    time: number;
    hasHighway: boolean;
    hasToll: boolean;
  };
}
