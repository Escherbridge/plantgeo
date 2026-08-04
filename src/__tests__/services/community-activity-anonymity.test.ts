import { describe, expect, it } from "vitest";
import {
  activityCellRange,
  gridCellDegrees,
} from "@/lib/server/services/community-activity";
import type { BoundingBox } from "@/lib/server/security/bbox";

/**
 * The differencing attack this rule exists to stop.
 *
 * `/api/v1/action-network` is unauthenticated and reports `featureCount` per cell behind a
 * `HAVING count(*) >= 3` floor. While the caller's bbox was pushed into the WHERE clause, that
 * floor filtered a set the caller had already trimmed: query one 0.01deg cell, then the same cell
 * minus a thin strip, and the difference tells you whether one contributor's private submission
 * lies in the strip. `parseBoundingBox` imposes no minimum extent, so ~40 requests per axis
 * resolve a submitted lat/lon to full float precision -- the exact point `community.getRequests`
 * restricts to the author and their workspace.
 *
 * The guarantee is NOT "shrinking a viewport never drops a cell" -- it may, and dropping a whole
 * cell leaks nothing. It is that **a row's membership in its cell is decided by the row's own
 * coordinate and never by the viewport**, so any cell that comes back comes back complete. That is
 * what makes two overlapping answers un-differenceable, and it is what these tests pin.
 */
const CELL_DEGREES = 0.01;

/** The `floor(coordinate / cellDegrees)` Postgres computes, in the same IEEE-754 doubles. */
function cellOf(coordinate: number, cellDegrees = CELL_DEGREES): number {
  return Math.floor(coordinate / cellDegrees);
}

/** The row filter the WHERE clause applies: `floor(lon / cell) BETWEEN westCell AND eastCell`. */
function acceptsPoint(
  range: ReturnType<typeof activityCellRange>,
  longitude: number,
  latitude: number,
  cellDegrees = CELL_DEGREES
): boolean {
  const longitudeCell = cellOf(longitude, cellDegrees);
  const latitudeCell = cellOf(latitude, cellDegrees);
  return (
    longitudeCell >= range.westCell &&
    longitudeCell <= range.eastCell &&
    latitudeCell >= range.southCell &&
    latitudeCell <= range.northCell
  );
}

describe("activityCellRange", () => {
  it("derives cell INDICES with the same expression the GROUP BY uses, not snapped coordinates", () => {
    // A snapped coordinate literal and floor() can disagree at a cell boundary; comparing indices
    // to indices cannot. Every edge below is checked against floor() itself rather than a constant.
    for (const west of [-125, -116.2065, -0.0051, 0, 0.0349, 43.6042, 179.9901]) {
      const range = activityCellRange([west, -1.234, west + 0.0237, 1.234], CELL_DEGREES);
      expect(range.westCell).toBe(cellOf(west));
      expect(range.eastCell).toBe(cellOf(west + 0.0237));
      expect(range.southCell).toBe(cellOf(-1.234));
      expect(range.northCell).toBe(cellOf(1.234));
    }
  });

  it("decides a point by its OWN cell, so two points in one cell are never separated", () => {
    // The whole attack is separating two points that share a cell. Sweep a family of viewports --
    // including thin strips an attacker would bisect with -- and assert that for every one of
    // them, every cell's sample points are accepted or rejected as a block.
    const viewports: BoundingBox[] = [];
    for (let step = 0; step < 12; step += 1) {
      const west = -116.2137 + step * 0.0011;
      viewports.push([west, 43.6013, west + 0.0007, 43.6019]);
      viewports.push([west, 43.6013, west + 0.0231, 43.6207]);
    }

    const samples: Array<[number, number]> = [];
    for (let longitudeStep = 0; longitudeStep < 40; longitudeStep += 1) {
      for (let latitudeStep = 0; latitudeStep < 6; latitudeStep += 1) {
        samples.push([-116.2149 + longitudeStep * 0.0009, 43.6007 + latitudeStep * 0.0043]);
      }
    }

    for (const viewport of viewports) {
      const range = activityCellRange(viewport, CELL_DEGREES);
      const verdictByCell = new Map<string, boolean>();
      for (const [longitude, latitude] of samples) {
        const cellKey = `${cellOf(longitude)}:${cellOf(latitude)}`;
        const accepted = acceptsPoint(range, longitude, latitude);
        const established = verdictByCell.get(cellKey);
        if (established === undefined) {
          verdictByCell.set(cellKey, accepted);
          continue;
        }
        // A single mismatch here is the leak: it means this viewport split a cell in half.
        expect({ viewport, cellKey, accepted }).toEqual({
          viewport,
          cellKey,
          accepted: established,
        });
      }
    }
  });

  it("gives two viewports covering the same cells identical bounds, however their edges sit", () => {
    const wide = activityCellRange([-116.2137, 43.6013, -116.2101, 43.6082], CELL_DEGREES);
    const narrow = activityCellRange([-116.2134, 43.6017, -116.2103, 43.6079], CELL_DEGREES);

    expect(cellOf(-116.2137)).toBe(cellOf(-116.2134));
    expect(cellOf(-116.2101)).toBe(cellOf(-116.2103));
    expect(narrow).toEqual(wide);
  });

  it("keeps the cell that spans the sign change whole, where floor() rounds away from zero", () => {
    // floor(-0.005 / 0.01) is -1, not 0: [-0.01, 0) is one cell and must not be split.
    const range = activityCellRange([-0.005, -0.005, 0.005, 0.005], CELL_DEGREES);

    expect(range.westCell).toBe(-1);
    expect(range.eastCell).toBe(0);
    expect(acceptsPoint(range, -0.009, -0.009)).toBe(true);
    expect(acceptsPoint(range, -0.001, -0.001)).toBe(true);
  });

  it("widens the sargable coordinate pre-filter past every cell the index bounds keep", () => {
    // The four coordinate comparisons exist only so Postgres can use an index; if they ever
    // trimmed a cell the index bounds kept, they would reintroduce the differencing hole.
    const range = activityCellRange([-116.2137, 43.6013, -116.2101, 43.6082], CELL_DEGREES);

    expect(range.minimumLongitude).toBeLessThan(range.westCell * CELL_DEGREES);
    expect(range.maximumLongitude).toBeGreaterThan((range.eastCell + 1) * CELL_DEGREES);
    expect(range.minimumLatitude).toBeLessThan(range.southCell * CELL_DEGREES);
    expect(range.maximumLatitude).toBeGreaterThan((range.northCell + 1) * CELL_DEGREES);
  });

  it("holds at the finest cell the zoom clamp allows, which is where the attack lives", () => {
    // zoom 22 is the maximum the route accepts; MINIMUM_CELL_DEGREES clamps the cell to 0.01deg,
    // so no caller can ask for a finer grid than the one exercised above.
    expect(gridCellDegrees(22)).toBe(CELL_DEGREES);
    expect(gridCellDegrees(0)).toBe(10);

    const sliver = activityCellRange([-116.2101, 43.6001, -116.2099, 43.6002], CELL_DEGREES);
    expect(sliver.westCell).toBe(cellOf(-116.2101));
    expect(sliver.eastCell).toBe(cellOf(-116.2099));
  });
});
