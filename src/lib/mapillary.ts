export interface MapillaryImage {
  id: string;
  geometry: GeoJSON.Point;
  thumbUrl: string;
  compassAngle: number;
  sequenceId: string;
}
