/**
 * Builds a byte-exact, minimal, empty PMTiles v3 archive.
 *
 * The app's `protomaps` vector source resolves through the `pmtiles://` custom protocol
 * (see src/components/map/MapView.tsx `maplibregl.addProtocol("pmtiles", ...)`), which fetches
 * the real archive over HTTPS (currently `https://tiles.aevani.com/...`) via byte-range GETs.
 * That request is intercepted in e2e/fixtures/network.ts and fulfilled with the bytes this
 * module produces, so tile loading never touches the real network -- and, because the crafted
 * root directory has zero entries, every z/x/y tile lookup resolves to "no data" instead of an
 * error, so MapLibre never fires the map "error" event this app's own handler logs via
 * console.error (see MapView.tsx's `m.on("error", ...)`).
 *
 * Layout follows the PMTiles v3 spec (https://github.com/protomaps/PMTiles/blob/main/spec/v3/spec.md),
 * cross-checked against the reader in node_modules/pmtiles/dist/pmtiles.js (`bytesToHeader`,
 * `getHeaderAndRoot`, `deserializeIndex`):
 *   - bytes 0-6:    magic "PMTiles"
 *   - byte 7:       spec version (3)
 *   - bytes 8-126:  fixed-width header fields (root/metadata/leaf/tile-data offsets+lengths,
 *                   counts, compression/tile-type bytes, zoom range, bounds, center)
 *   - byte 127:     the root directory, a single varint 0x00 -- "zero entries"
 *
 * `internalCompression` and `tileCompression` are both set to `1` (None) so the reader never
 * needs to gunzip anything. `tileType` is `1` (Mvt) so a missing-tile lookup resolves to an
 * empty (but valid) vector tile rather than a thrown error. Bounds are set to a real, valid
 * range (minLon < maxLon, minLat < maxLat) because the protocol handler itself
 * `console.error`s when they are not.
 */
const HEADER_SIZE_BYTES = 127;
const ROOT_DIRECTORY_OFFSET = HEADER_SIZE_BYTES;
const ROOT_DIRECTORY_LENGTH = 1;
const ARCHIVE_SIZE_BYTES = HEADER_SIZE_BYTES + ROOT_DIRECTORY_LENGTH;

function writeUint64LE(view: DataView, offset: number, value: number): void {
  // Every field this fixture needs fits well inside 32 bits -- BigInt is unnecessary.
  view.setUint32(offset, value, true);
  view.setUint32(offset + 4, 0, true);
}

export function buildEmptyPmtilesArchive(): Uint8Array {
  const buffer = new ArrayBuffer(ARCHIVE_SIZE_BYTES);
  const bytes = new Uint8Array(buffer);
  const view = new DataView(buffer);

  const magic = "PMTiles";
  for (let i = 0; i < magic.length; i++) bytes[i] = magic.charCodeAt(i);
  view.setUint8(7, 3); // spec version

  writeUint64LE(view, 8, ROOT_DIRECTORY_OFFSET);
  writeUint64LE(view, 16, ROOT_DIRECTORY_LENGTH);
  writeUint64LE(view, 24, 0); // jsonMetadataOffset (unused -- Protocol() defaults metadata:false)
  writeUint64LE(view, 32, 0); // jsonMetadataLength
  writeUint64LE(view, 40, 0); // leafDirectoryOffset
  writeUint64LE(view, 48, 0); // leafDirectoryLength
  writeUint64LE(view, 56, 0); // tileDataOffset
  writeUint64LE(view, 64, 0); // tileDataLength
  writeUint64LE(view, 72, 0); // numAddressedTiles
  writeUint64LE(view, 80, 0); // numTileEntries
  writeUint64LE(view, 88, 0); // numTileContents

  view.setUint8(96, 0); // clustered: false
  view.setUint8(97, 1); // internalCompression: None
  view.setUint8(98, 1); // tileCompression: None
  view.setUint8(99, 1); // tileType: Mvt
  view.setUint8(100, 0); // minZoom
  view.setUint8(101, 15); // maxZoom

  view.setInt32(102, -180 * 1e7, true); // minLon
  view.setInt32(106, -85 * 1e7, true); // minLat
  view.setInt32(110, 180 * 1e7, true); // maxLon
  view.setInt32(114, 85 * 1e7, true); // maxLat
  view.setUint8(118, 5); // centerZoom
  view.setInt32(119, -118 * 1e7, true); // centerLon
  view.setInt32(123, 45.5 * 1e7, true); // centerLat

  // Root directory: a single varint-encoded 0 -- "zero tile entries".
  bytes[ROOT_DIRECTORY_OFFSET] = 0x00;

  return bytes;
}
