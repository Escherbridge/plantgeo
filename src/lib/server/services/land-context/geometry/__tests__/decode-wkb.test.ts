/**
 * Fixtures below are hand-computed per the OGC WKB spec (little-endian byte
 * order flag `01`, then a uint32 geometry-type code: 1=Point, 3=Polygon,
 * 6=MultiPolygon, each followed by IEEE-754 float64 ordinates) and verified
 * against PostGIS's documented `ST_AsBinary`/`ST_AsEWKB` output shape for
 * the same WKT:
 *   - `POINT(1 2)` LE  -> matches `ST_AsBinary(ST_GeomFromText('POINT(1 2)'))`
 *   - `POINT(1 2)` BE  -> big-endian byte-order flag `00`, same ordinates
 *   - `POINT(1 2)` EWKB SRID 4326 -> matches
 *     `ST_AsEWKB(ST_SetSRID(ST_GeomFromText('POINT(1 2)'), 4326))`,
 *     i.e. type word `00000001 | 0x20000000 = 0x20000001` (byte-swapped to
 *     `01000020` on the wire for LE), followed by SRID `e6100000` (4326 LE).
 *   - `POLYGON((0 0,4 0,4 4,0 4,0 0))` and the two-part MultiPolygon are
 *     built the same way: ring-count/point-count uint32s plus float64 pairs,
 *     assembled by hand from the WKB grammar rather than pulled from a
 *     random-looking byte string.
 */
import { describe, expect, it } from "vitest";
import { decodeWkb } from "../decode-wkb";
import { WkbDecodeError } from "../wkb-decode-error";

describe("decodeWkb", () => {
  it("decodes a little-endian WKB Point", () => {
    const geometry = decodeWkb("0101000000000000000000f03f0000000000000040");
    expect(geometry).toEqual({ type: "Point", coordinates: [1, 2] });
  });

  it("decodes a big-endian WKB Point", () => {
    const geometry = decodeWkb("00000000013ff00000000000004000000000000000");
    expect(geometry).toEqual({ type: "Point", coordinates: [1, 2] });
  });

  it("decodes an EWKB Point with an SRID prefix, skipping the SRID", () => {
    const geometry = decodeWkb("0101000020e6100000000000000000f03f0000000000000040");
    expect(geometry).toEqual({ type: "Point", coordinates: [1, 2] });
  });

  it("decodes a WKB Polygon", () => {
    const geometry = decodeWkb(
      "010300000001000000050000000000000000000000000000000000000000000000000010400000000000000000000000000000104000000000000010400000000000000000000000000000104000000000000000000000000000000000"
    );
    expect(geometry).toEqual({
      type: "Polygon",
      coordinates: [
        [
          [0, 0],
          [4, 0],
          [4, 4],
          [0, 4],
          [0, 0],
        ],
      ],
    });
  });

  it("decodes a WKB MultiPolygon with two member polygons", () => {
    const geometry = decodeWkb(
      "010600000002000000010300000001000000050000000000000000000000000000000000000000000000000010400000000000000000000000000000104000000000000010400000000000000000000000000000104000000000000000000000000000000000010300000001000000050000000000000000002440000000000000244000000000000028400000000000002440000000000000284000000000000028400000000000002440000000000000284000000000000024400000000000002440"
    );
    expect(geometry).toEqual({
      type: "MultiPolygon",
      coordinates: [
        [
          [
            [0, 0],
            [4, 0],
            [4, 4],
            [0, 4],
            [0, 0],
          ],
        ],
        [
          [
            [10, 10],
            [12, 10],
            [12, 12],
            [10, 12],
            [10, 10],
          ],
        ],
      ],
    });
  });

  it("accepts a Buffer input equivalently to the hex string", () => {
    const hex = "0101000000000000000000f03f0000000000000040";
    const fromHex = decodeWkb(hex);
    const fromBuffer = decodeWkb(Buffer.from(hex, "hex"));
    expect(fromBuffer).toEqual(fromHex);
  });

  it("throws a typed WkbDecodeError, not a generic Error, on truncated input", () => {
    // Byte-order flag + type word present, but the Point's float64 ordinates
    // are missing entirely.
    const truncated = "0101000000";
    expect(() => decodeWkb(truncated)).toThrow(WkbDecodeError);
    try {
      decodeWkb(truncated);
      throw new Error("expected decodeWkb to throw");
    } catch (error) {
      expect(error).toBeInstanceOf(WkbDecodeError);
      expect((error as WkbDecodeError).reason).toBe("unexpected_end_of_buffer");
    }
  });

  it("throws WkbDecodeError on an invalid hex string", () => {
    expect(() => decodeWkb("not-hex-at-all")).toThrow(WkbDecodeError);
  });

  it("throws WkbDecodeError on an unsupported geometry type code", () => {
    // Byte-order flag (LE) + type word 99 (not a supported type).
    const unsupported = "0163000000";
    expect(() => decodeWkb(unsupported)).toThrow(WkbDecodeError);
  });

  it("throws WkbDecodeError on empty input", () => {
    expect(() => decodeWkb("")).toThrow(WkbDecodeError);
    expect(() => decodeWkb(Buffer.alloc(0))).toThrow(WkbDecodeError);
  });

  it("throws WkbDecodeError on trailing bytes after a complete geometry", () => {
    const pointWithTrailingGarbage = `${"0101000000000000000000f03f0000000000000040"}ff`;
    expect(() => decodeWkb(pointWithTrailingGarbage)).toThrow(WkbDecodeError);
  });
});
