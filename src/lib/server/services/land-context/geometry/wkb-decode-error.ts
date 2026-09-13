/**
 * Typed decode failure for the WKB/EWKB -> GeoJSON decoder in this
 * directory. Callers must be able to distinguish "the bytes we were given
 * are malformed" from "there is no geometry to decode" (the latter is a
 * `null` return, never a throw — see `decodeBoundaryGeometry` in
 * `./boundary-geometry-adapter.ts`).
 */
export type WkbDecodeErrorReason =
  | "empty_input"
  | "invalid_hex"
  | "unexpected_end_of_buffer"
  | "unsupported_byte_order"
  | "unsupported_geometry_type"
  | "trailing_bytes";

export class WkbDecodeError extends Error {
  readonly reason: WkbDecodeErrorReason;

  constructor(reason: WkbDecodeErrorReason, message: string) {
    super(message);
    this.name = "WkbDecodeError";
    this.reason = reason;
  }
}
