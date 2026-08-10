export type MapStyle = "dark" | "light" | "satellite";

export interface Viewport {
  longitude: number;
  latitude: number;
  zoom: number;
  bearing: number;
  pitch: number;
  /**
   * The map container's size in CSS pixels, which is what turns a centre and a zoom into the
   * ground the reader can actually see. Viewport-scoped queries used to assume a fixed
   * 1024x512 window: on any wider display that fetched a rectangle smaller than the screen, so
   * proxied features stopped at an invisible edge and the ground beyond it painted empty --
   * indistinguishable from ground with no data. Seeded with that historical assumption so a
   * viewport built before the map mounts behaves exactly as it used to.
   */
  widthPx: number;
  heightPx: number;
}

export type {
  StyleSpecification,
  LayerSpecification,
  PropertyValueSpecification,
  DataDrivenPropertyValueSpecification,
  ExpressionSpecification,
  FillExtrusionLayerSpecification,
} from "@maplibre/maplibre-gl-style-spec";

