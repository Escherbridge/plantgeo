/**
 * Pure WKB/EWKB -> GeoJSON geometry decoder.
 *
 * Hand-rolled buffer reader, not a library: `wkx`/`@turf/*`/`wellknown` are
 * not dependencies of this repo (checked `package.json` before writing
 * this), and the surface needed here is narrow (Point, LineString, Polygon,
 * MultiPolygon — the four types boundary/parcel/territory data actually
 * uses per `src/lib/server/db/schema/land-context/boundary-versions.ts`),
 * so a small dependency-free reader was preferred over adding a library for
 * four geometry types.
 *
 * Mirrors this codebase's existing WKB-as-string precedent: PostGIS/Drizzle
 * geometry columns here (`spatialGeometry` in
 * `src/lib/server/db/schema.ts` and its land-context mirror in
 * `db/schema/land-context/shared.ts`) round-trip geometry as WKB hex
 * strings via the driver, not as a distinct binary type — this module
 * accepts that same hex-string shape (or a raw `Buffer`, for callers who
 * already have bytes) and never invents a new geometry wire format.
 *
 * Supports both plain WKB (a plain 2D header: byte order + uint32 geometry
 * type) and PostGIS EWKB (a high bit on the geometry type signals an SRID
 * field immediately follows the type; that SRID is skipped, not
 * re-projected — MapLibre/GeoJSON consumers downstream assume EPSG:4326 per
 * `crs` on `boundaryVersions`, matching this table's documented default).
 */

import { WkbDecodeError } from "./wkb-decode-error";

type ByteOrder = "big" | "little";

const WKB_GEOMETRY_TYPE = {
  Point: 1,
  LineString: 2,
  Polygon: 3,
  MultiPolygon: 6,
} as const;

// PostGIS EWKB sets bit 0x20000000 on the geometry-type word when an SRID
// field follows. Plain (ISO) WKB never sets this bit.
const EWKB_SRID_FLAG = 0x20000000;
// Mask off the SRID flag (and the unused Z/M flag bits some producers set)
// to recover the base 2D geometry type code.
const WKB_TYPE_MASK = 0xff;

class ByteReader {
  private readonly view: DataView;
  private offset = 0;
  private order: ByteOrder = "big";

  constructor(private readonly buffer: Buffer) {
    this.view = new DataView(buffer.buffer, buffer.byteOffset, buffer.byteLength);
  }

  get bytesRemaining(): number {
    return this.buffer.byteLength - this.offset;
  }

  private ensure(bytes: number): void {
    if (this.bytesRemaining < bytes) {
      throw new WkbDecodeError(
        "unexpected_end_of_buffer",
        `expected ${bytes} more byte(s) at offset ${this.offset}, only ${this.bytesRemaining} remain`
      );
    }
  }

  readByteOrder(): ByteOrder {
    this.ensure(1);
    const flag = this.view.getUint8(this.offset);
    this.offset += 1;
    if (flag === 0) {
      this.order = "big";
    } else if (flag === 1) {
      this.order = "little";
    } else {
      throw new WkbDecodeError(
        "unsupported_byte_order",
        `unrecognized WKB byte-order flag 0x${flag.toString(16)}`
      );
    }
    return this.order;
  }

  readUint32(): number {
    this.ensure(4);
    const value = this.view.getUint32(this.offset, this.order === "little");
    this.offset += 4;
    return value;
  }

  readFloat64(): number {
    this.ensure(8);
    const value = this.view.getFloat64(this.offset, this.order === "little");
    this.offset += 8;
    return value;
  }
}

/** [longitude, latitude] — Z/M ordinates, if present upstream, are not read (2D-only per this table's `geometry(GEOMETRY,4326)` columns). */
type Position = [number, number];

function readPosition(reader: ByteReader): Position {
  const x = reader.readFloat64();
  const y = reader.readFloat64();
  return [x, y];
}

function readPositionArray(reader: ByteReader): Position[] {
  const count = reader.readUint32();
  const positions: Position[] = new Array(count);
  for (let i = 0; i < count; i += 1) {
    positions[i] = readPosition(reader);
  }
  return positions;
}

function readLinearRingArray(reader: ByteReader): Position[][] {
  const ringCount = reader.readUint32();
  const rings: Position[][] = new Array(ringCount);
  for (let i = 0; i < ringCount; i += 1) {
    rings[i] = readPositionArray(reader);
  }
  return rings;
}

/**
 * Reads one geometry (its own byte-order + type header, recursively for
 * container types) starting at the reader's current offset.
 */
function readGeometry(reader: ByteReader): GeoJSON.Geometry {
  reader.readByteOrder();
  const rawType = reader.readUint32();
  const hasSrid = (rawType & EWKB_SRID_FLAG) !== 0;
  const type = rawType & WKB_TYPE_MASK;
  if (hasSrid) {
    // EWKB SRID field: skip, do not re-project. Callers assume EPSG:4326.
    reader.readUint32();
  }

  switch (type) {
    case WKB_GEOMETRY_TYPE.Point: {
      const coordinates = readPosition(reader);
      return { type: "Point", coordinates };
    }
    case WKB_GEOMETRY_TYPE.LineString: {
      const coordinates = readPositionArray(reader);
      return { type: "LineString", coordinates };
    }
    case WKB_GEOMETRY_TYPE.Polygon: {
      const coordinates = readLinearRingArray(reader);
      return { type: "Polygon", coordinates };
    }
    case WKB_GEOMETRY_TYPE.MultiPolygon: {
      const polygonCount = reader.readUint32();
      const coordinates: Position[][][] = new Array(polygonCount);
      for (let i = 0; i < polygonCount; i += 1) {
        // Each member polygon is itself a full WKB/EWKB geometry (its own
        // byte-order + type header) per the OGC WKB spec for collection
        // types; read and unwrap it rather than assuming a bare ring list.
        const member = readGeometry(reader);
        if (member.type !== "Polygon") {
          throw new WkbDecodeError(
            "unsupported_geometry_type",
            `MultiPolygon member ${i} was decoded as ${member.type}, expected Polygon`
          );
        }
        coordinates[i] = member.coordinates as Position[][];
      }
      return { type: "MultiPolygon", coordinates };
    }
    default:
      throw new WkbDecodeError(
        "unsupported_geometry_type",
        `unsupported WKB geometry type code ${type} (raw 0x${rawType.toString(16)})`
      );
  }
}

const HEX_PATTERN = /^[0-9a-fA-F]+$/;

function toBuffer(input: Buffer | string): Buffer {
  if (Buffer.isBuffer(input)) {
    if (input.byteLength === 0) {
      throw new WkbDecodeError("empty_input", "WKB input buffer is empty");
    }
    return input;
  }

  const trimmed = input.trim();
  if (trimmed.length === 0) {
    throw new WkbDecodeError("empty_input", "WKB input string is empty");
  }
  if (trimmed.length % 2 !== 0 || !HEX_PATTERN.test(trimmed)) {
    throw new WkbDecodeError(
      "invalid_hex",
      "WKB input string is not valid even-length hexadecimal"
    );
  }
  return Buffer.from(trimmed, "hex");
}

/**
 * Decodes a WKB or EWKB geometry (hex string or raw `Buffer`) into a
 * GeoJSON geometry. Supports Point, LineString, Polygon, and MultiPolygon
 * in either byte order. Throws {@link WkbDecodeError} — never a bare
 * `Error` — on malformed input, so callers can distinguish "bad geometry
 * data" from "no data" (the latter is handled by
 * `decodeBoundaryGeometry` in `./boundary-geometry-adapter.ts`, which
 * returns `null` for a genuinely absent value instead of calling this
 * function at all).
 */
export function decodeWkb(input: Buffer | string): GeoJSON.Geometry {
  const buffer = toBuffer(input);
  const reader = new ByteReader(buffer);
  const geometry = readGeometry(reader);
  if (reader.bytesRemaining > 0) {
    throw new WkbDecodeError(
      "trailing_bytes",
      `${reader.bytesRemaining} unexpected trailing byte(s) after a complete geometry`
    );
  }
  return geometry;
}
