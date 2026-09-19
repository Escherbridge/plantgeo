// @vitest-environment node
// jsdom (this repo's default environment) does not resolve `import.meta.url` to a real `file:`
// URL, so `fileURLToPath` below throws "The URL must be of scheme file"; this file needs the real
// filesystem to walk `src/`, same reason `manifest-parity.test.ts` carries the same pragma.
// Stray-literal guard: fails on a new WGS84 footprint literal outside the region manifest.
// `conductor/code_styleguides/federation.md` §1 "Permitted literal coordinates" and §5 step 4.
// Regex-scans every `src/**/*.{ts,tsx}` file (excluding tests and the region manifests themselves,
// `src/lib/region/{pnw,kenya_highlands}.ts`, which ARE the declaration) for a
// 4-number bbox string/array/object that reads as a WGS84 footprint (either hemisphere), and for
// the `US-WA`/`US-OR`/`US-ID`/`Pacific Northwest`/`PNW` markers. See `AGENTS.md` in this directory
// for why the scan targets declared literals rather than every occurrence of the word "bbox", and
// for the false positives that scoping choice was built to avoid.
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

// Resolved from `import.meta.url` rather than the bare relative string "src" (NIT 5, W3 review):
// the Python guard already anchors on `Path(__file__).resolve().parents[1]`, and a cwd-relative
// path here depends on vitest's own working directory rather than this file's location.
const SRC_ROOT = join(fileURLToPath(new URL(".", import.meta.url)), "..", "..");

const EXCLUDED_DIRECTORY_NAMES = new Set(["__tests__", "__benchmarks__", "node_modules"]);

/** Files that ARE the manifest's own declaration -- never "a literal outside the manifest". */
const ALLOWED_RELATIVE_PATHS = new Set([
  "lib/region/pnw.ts",
  "lib/region/kenya_highlands.ts",
  "lib/region/region.ts",
]);

/** A world-extent sentinel string or array; federation.md §1's explicit "no viewport" exception. */
const WORLD_EXTENT_STRING = "-180,-90,180,90";
const WORLD_EXTENT_ARRAY: readonly number[] = [-180, -90, 180, 90];

const ADMIN_CODE_PATTERN = /\bUS-(WA|OR|ID)\b/;
const REGION_WORD_PATTERN = /\b(Pacific Northwest|PNW)\b/;

/**
 * A footprint-hinting name -- mirrors the Python guard's `_NAME_HINT_PATTERN`. Searched in the
 * text immediately preceding a bare array/string 4-number literal (its declaring identifier or an
 * object key) since the regex scan has no AST to read an assignment target from directly.
 */
const NAME_HINT_PATTERN = /\b(bbox|envelope|bounds|extent|lat|lon|longitude|latitude)\b/i;

/** How far back (characters) to look for a footprint-hinting name before a bare array/string literal. */
const NAME_HINT_CONTEXT_WINDOW = 80;

/**
 * A region footprint is between this wide and this narrow on each axis -- narrower than a state,
 * wider than a neighbourhood. Zoom ladders and colour tuples are excluded by their own explicit
 * predicates below, not by this range, so this only has to be "plausible for a region".
 */
const MIN_PLAUSIBLE_REGION_SPAN_DEGREES = 0.5;
const MAX_PLAUSIBLE_REGION_SPAN_DEGREES = 60;

/** The highest zoom tier this codebase's ladders ever declare; used only to size the zoom-ladder exclusion. */
const MAX_PLAUSIBLE_ZOOM_LADDER_VALUE = 24;

/**
 * A string this long is prose (a comment sentence, a citation), not a declared literal; capping
 * length keeps the region-word/admin-code check from matching sentences that merely mention the
 * pilot region rather than restating its footprint.
 */
const MAX_DECLARED_STRING_LENGTH = 40;

interface FoundOffender {
  path: string;
  line: number;
  description: string;
}

function listSourceFiles(directory: string): string[] {
  const entries = readdirSync(directory);
  const files: string[] = [];
  for (const entry of entries) {
    if (EXCLUDED_DIRECTORY_NAMES.has(entry)) continue;
    const fullPath = join(directory, entry);
    const stats = statSync(fullPath);
    if (stats.isDirectory()) {
      files.push(...listSourceFiles(fullPath));
    } else if (/\.(ts|tsx)$/.test(entry) && !/\.test\.(ts|tsx)$/.test(entry)) {
      files.push(fullPath);
    }
  }
  return files;
}

/** All four values are integers in the 0-255 RGBA byte range -- a colour tuple, not a footprint. */
function isPlausibleColorTuple(values: readonly number[]): boolean {
  return values.every((value) => Number.isInteger(value) && value >= 0 && value <= 255);
}

/** Strictly increasing small integers -- a zoom-tier ladder (e.g. `[0, 5, 9, 13]`), not a footprint. */
function isPlausibleZoomLadder(values: readonly number[]): boolean {
  if (!values.every((value) => Number.isInteger(value) && value >= 0 && value <= MAX_PLAUSIBLE_ZOOM_LADDER_VALUE)) {
    return false;
  }
  return values.every((value, index) => index === 0 || value > values[index - 1]);
}

/**
 * True when `west/south/east/north` reads as a WGS84 footprint, in either hemisphere, and is not
 * a coincidental 4-in-range-number literal. Structurally in-range and ordered is necessary but not
 * sufficient -- zoom ladders and RGBA colour arrays clear that bar too -- so a candidate is a
 * footprint only when it is ALSO not one of those explicit shapes AND at least one of: it carries
 * literal `west`/`south`/`east`/`north` keys, it is declared near a footprint-hinting name, or its
 * span is the size a region actually is (not a state, not a neighbourhood).
 */
function looksLikeFootprint(
  west: number,
  south: number,
  east: number,
  north: number,
  hasNamedKeys: boolean,
  precedingContext: string
): boolean {
  if (!Number.isFinite(west) || !Number.isFinite(south) || !Number.isFinite(east) || !Number.isFinite(north)) {
    return false;
  }
  if (west < -180 || west > 180 || east < -180 || east > 180) return false;
  if (south < -90 || south > 90 || north < -90 || north > 90) return false;
  if (!(west < east && south < north)) return false;

  const values = [west, south, east, north];
  if (isPlausibleColorTuple(values)) return false;
  if (isPlausibleZoomLadder(values)) return false;

  if (hasNamedKeys) return true;
  // `\b` treats `_` as a word character, so `SOME_ENVELOPE` never reaches a boundary around
  // `ENVELOPE`; normalising underscores to spaces first restores the hint for
  // `snake_case`/`SCREAMING_SNAKE_CASE` identifiers, same fix as the Python guard's.
  if (NAME_HINT_PATTERN.test(precedingContext.replace(/_/g, " "))) return true;

  const lonSpan = east - west;
  const latSpan = north - south;
  return (
    lonSpan >= MIN_PLAUSIBLE_REGION_SPAN_DEGREES &&
    lonSpan <= MAX_PLAUSIBLE_REGION_SPAN_DEGREES &&
    latSpan >= MIN_PLAUSIBLE_REGION_SPAN_DEGREES &&
    latSpan <= MAX_PLAUSIBLE_REGION_SPAN_DEGREES
  );
}

const BBOX_STRING_PATTERN = /"(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)"/g;
// Trailing comma+whitespace before the closing bracket/brace: a Prettier-formatted multi-line
// literal always ends `north: 49,\n};` / `49,\n]`, with the comma standing between the last
// number and the closer -- the original patterns required the closer immediately after the last
// number and so never matched any multi-line literal this repo actually formats.
const BBOX_ARRAY_PATTERN =
  /\[\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*,?\s*\]/g;
const BBOX_OBJECT_PATTERN =
  /\{\s*west:\s*(-?\d+(?:\.\d+)?)\s*,\s*south:\s*(-?\d+(?:\.\d+)?)\s*,\s*east:\s*(-?\d+(?:\.\d+)?)\s*,\s*north:\s*(-?\d+(?:\.\d+)?)\s*,?\s*\}/g;
const STRING_LITERAL_PATTERN = /"([^"\\]|\\.)*"|'([^'\\]|\\.)*'/g;

function lineNumberAt(source: string, index: number): number {
  return source.slice(0, index).split("\n").length;
}

/**
 * Blanks out `//` and `/* *\/` comments, character-for-character (newlines preserved so line
 * numbers stay correct), while leaving string/template literals untouched -- otherwise a JSDoc
 * sentence like `coverage-region.ts`'s `a PNW box reads "Pacific Northwest" rather than...` would
 * flag as a code literal. Tries string/template-literal alternatives before the comment
 * alternatives at every position, so a `//` inside a quoted URL is consumed as string content
 * before the comment branch ever sees it -- the standard trick for stripping comments without a
 * full tokenizer. Known gap: a `/.../ ` regex literal containing a quote-like character could
 * confuse this; none exists in this source tree today.
 */
function stripComments(source: string): string {
  return source.replace(
    /"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|`(?:[^`\\]|\\.)*`|\/\/[^\n]*|\/\*[\s\S]*?\*\//g,
    (match) => (match.startsWith("//") || match.startsWith("/*") ? match.replace(/[^\n]/g, " ") : match)
  );
}

function scanFile(relativePath: string, rawSource: string): FoundOffender[] {
  const offenders: FoundOffender[] = [];
  const source = stripComments(rawSource);

  for (const [pattern, hasNamedKeys] of [
    [BBOX_STRING_PATTERN, false],
    [BBOX_ARRAY_PATTERN, false],
    [BBOX_OBJECT_PATTERN, true],
  ] as const) {
    pattern.lastIndex = 0;
    let match: RegExpExecArray | null;
    while ((match = pattern.exec(source)) !== null) {
      const [, w, s, e, n] = match;
      const west = Number(w);
      const south = Number(s);
      const east = Number(e);
      const north = Number(n);
      if (match[0] === `"${WORLD_EXTENT_STRING}"`) continue;
      if ([west, south, east, north].every((value, index) => value === WORLD_EXTENT_ARRAY[index])) continue;
      const precedingContext = source.slice(Math.max(0, match.index - NAME_HINT_CONTEXT_WINDOW), match.index);
      if (looksLikeFootprint(west, south, east, north, hasNamedKeys, precedingContext)) {
        offenders.push({
          path: relativePath,
          line: lineNumberAt(source, match.index),
          description: `footprint literal ${match[0]}`,
        });
      }
    }
  }

  STRING_LITERAL_PATTERN.lastIndex = 0;
  let stringMatch: RegExpExecArray | null;
  while ((stringMatch = STRING_LITERAL_PATTERN.exec(source)) !== null) {
    const literal = stringMatch[0];
    const text = literal.slice(1, -1);
    if (text.length > MAX_DECLARED_STRING_LENGTH) continue;
    if (ADMIN_CODE_PATTERN.test(text) || REGION_WORD_PATTERN.test(text)) {
      offenders.push({
        path: relativePath,
        line: lineNumberAt(source, stringMatch.index),
        description: `region-name/admin-code literal ${literal}`,
      });
    }
  }

  return offenders;
}

/**
 * Known offenders, enumerated by `path:description` (the description already contains the
 * offending value, which is what identifies the literal -- NOT the line number, which shifts on
 * any unrelated edit above it and fails this test in both directions for nobody's benefit).
 * Migrated per federation.md §1 (moved "into or behind the manifest") and §5 step 2b
 * (`coverage-region.ts`'s non-PNW rows, staying literal "until a future multi-region manifest
 * registry exists to read them from" per `src/lib/region/AGENTS.md` "Remaining footprint literals
 * `coverage-region.ts` still carries"). Removing an entry once its value reads from `getRegion()`
 * is the only edit this set may receive; a new offender elsewhere fails this test rather than
 * growing this list silently.
 */
const KNOWN_OFFENDERS = new Set<string>([
  // `coverage-region.ts`'s "California"/"Western United States"/"North America" rows describe
  // footprints this deployment does not serve; there is no manifest entry to point them at yet.
  "lib/map/coverage-region.ts:footprint literal { west: -125, south: 32, east: -114, north: 42.5 }",
  "lib/map/coverage-region.ts:footprint literal { west: -126, south: 31, east: -102, north: 50 }",
  "lib/map/coverage-region.ts:footprint literal { west: -170, south: 14, east: -52, north: 72 }",
  // The offline-download default bbox is its own narrower footprint (south 45/east -116, not the
  // manifest's 42/-111); reading `getRegion().defaultCameraEnvelope` here would silently widen the
  // pre-existing default download area, which this guard is not authorised to do. Its sibling
  // fallback three lines below (`{ west: w || -125, south: s || 45, ... }`) is not a literal this
  // pattern matches -- the value after each key is a `||` expression, not a bare number -- so it
  // is not (and should not become) a second entry here.
  "components/panels/OfflinePanel.tsx:footprint literal { west: -125, south: 45, east: -116, north: 49 }",
]);

function offenderKey(offender: FoundOffender): string {
  return `${offender.path}:${offender.description}`;
}

function scanAllSourceFiles(): FoundOffender[] {
  const files = listSourceFiles(SRC_ROOT);
  const found: FoundOffender[] = [];
  for (const filePath of files) {
    const relativePath = relative(SRC_ROOT, filePath).split(sep).join("/");
    if (ALLOWED_RELATIVE_PATHS.has(relativePath)) continue;
    const source = readFileSync(filePath, "utf8");
    found.push(...scanFile(relativePath, source));
  }
  return found;
}

describe("stray footprint literal guard", () => {
  it("finds no footprint/admin-code/region-name literal outside the manifest or KNOWN_OFFENDERS", () => {
    const found = new Map<string, FoundOffender>();
    for (const offender of scanAllSourceFiles()) {
      found.set(offenderKey(offender), offender);
    }

    const newOffenders = [...found.keys()].filter((key) => !KNOWN_OFFENDERS.has(key));
    expect(
      newOffenders,
      "New footprint/admin-code/region-name literal(s) found outside the region manifest " +
        "(federation.md §1). Either read the value from `getRegion()` in a behaviour-neutral " +
        "one-line change, or add it to KNOWN_OFFENDERS with a citation. " +
        `Lines: ${newOffenders.map((key) => `${key} (line ${found.get(key)?.line})`).join("; ")}`
    ).toEqual([]);

    const staleKnownOffenders = [...KNOWN_OFFENDERS].filter((key) => !found.has(key));
    expect(
      staleKnownOffenders,
      "KNOWN_OFFENDERS lists an entry this scan no longer finds; remove the stale entry rather " +
        "than let the debt list grow unverifiable."
    ).toEqual([]);
  });

  // B2 self-test: the guard's own coverage was never pinned by a test that plants a literal it
  // must find, which is how a multi-line-object regression (the trailing-comma gap this same fix
  // closes) shipped invisible. See useLandContextViewport.ts:117-122, already fixed in this tree
  // (wave 4) to read WORLD_EXTENT_ENVELOPE rather than declare the burn-severity envelope inline.
  it("finds a single-line footprint object literal", () => {
    const fixture = `export const SOME_BOX = { west: -125, south: 42, east: -111, north: 49 };\n`;
    const found = scanFile("fixture.ts", fixture);
    expect(found.some((offender) => offender.description.includes("west: -125"))).toBe(true);
  });

  it("finds a multi-line, Prettier-formatted footprint object literal (the B2 regression)", () => {
    const fixture = [
      "export const SOME_BOX = {",
      "  west: -125,",
      "  south: 42,",
      "  east: -111,",
      "  north: 49,",
      "};",
      "",
    ].join("\n");
    const found = scanFile("fixture.ts", fixture);
    expect(found.some((offender) => offender.description.includes("west: -125"))).toBe(true);
  });

  it("finds a multi-line footprint array literal with a trailing comma", () => {
    const fixture = ["export const SOME_ENVELOPE = [", "  -125, 42, -111, 49,", "];", ""].join("\n");
    const found = scanFile("fixture.ts", fixture);
    expect(found.some((offender) => offender.description.includes("-125"))).toBe(true);
  });

  // S1 self-test: an eastern-hemisphere footprint must be caught too -- the old hemisphere
  // discriminator could only ever police the pilot's own (western) hemisphere.
  it("finds an eastern-hemisphere footprint literal (a fabricated Kenya box)", () => {
    const fixture = `export const KENYA_ENVELOPE = { west: 34.0, south: -1.5, east: 38.0, north: 1.5 };\n`;
    const found = scanFile("fixture.ts", fixture);
    expect(found.some((offender) => offender.description.includes("west: 34"))).toBe(true);
  });

  it("finds an eastern-hemisphere footprint array literal near a footprint-hinting name", () => {
    const fixture = `export const KENYA_REGION = { bounds: [34.0, -1.5, 38.0, 1.5] };\n`;
    const found = scanFile("fixture.ts", fixture);
    expect(found.some((offender) => offender.description.includes("34"))).toBe(true);
  });

  it("ignores a colour tuple (four in-range integers, 0-255 RGBA)", () => {
    const fixture = `export const HIGHLIGHT_COLOR = [12, 34, 56, 78];\n`;
    const found = scanFile("fixture.ts", fixture);
    expect(found).toEqual([]);
  });

  it("ignores a zoom-tier ladder (strictly increasing small integers)", () => {
    const fixture = `export const ZOOM_TIERS = [0, 5, 9, 13];\n`;
    const found = scanFile("fixture.ts", fixture);
    expect(found).toEqual([]);
  });
});
