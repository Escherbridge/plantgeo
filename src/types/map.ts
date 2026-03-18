export type MapStyle = "dark" | "light" | "satellite";

export interface Viewport {
  longitude: number;
  latitude: number;
  zoom: number;
  bearing: number;
  pitch: number;
}

export type {
  StyleSpecification,
  LayerSpecification,
  PropertyValueSpecification,
  DataDrivenPropertyValueSpecification,
  ExpressionSpecification,
  FillExtrusionLayerSpecification,
} from "@maplibre/maplibre-gl-style-spec";

