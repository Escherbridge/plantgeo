/** The "no viewport" sentinel bbox: the whole world, west/south/east/north degrees. */
export const WORLD_EXTENT_BBOX = "-180,-90,180,90";

/**
 * The same sentinel in object form, converted here exactly once so no caller re-types the four
 * numbers. `federation.md` §1 permits the world envelope and nothing narrower as a literal box.
 */
export const WORLD_EXTENT_ENVELOPE = Object.freeze({
  west: -180,
  south: -90,
  east: 180,
  north: 90,
});
