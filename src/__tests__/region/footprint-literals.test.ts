// Stray-literal guard: fails on a new WGS84 footprint literal outside the region manifest.
// `conductor/code_styleguides/federation.md` §1 "Permitted literal coordinates" and §5 step 4.
// Regex-scans every `src/**/*.{ts,tsx}` file (excluding tests and `src/lib/region/pnw.ts`) for a
// 4-number bbox string/array/object that reads as a western-hemisphere WGS84 footprint, and for
// the `US-WA`/`US-OR`/`US-ID`/`Pacific Northwest`/`PNW` markers. See `AGENTS.md` in this directory
// for why the scan targets declared literals rather than every occurrence of the word "bbox", and
// for the false positives that scoping choice was built to avoid.
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { describe, expect, it } from "vitest";

const SRC_ROOT = "src";

const EXCLUDED_DIRECTORY_NAMES = new Set(["__tests__", "__benchmarks__", "node_modules"]);

/** Files that ARE the manifest's own declaration -- never "a literal outside the manifest". */
const ALLOWED_RELATIVE_PATHS = new Set(["lib/region/pnw.ts", "lib/region/region.ts"]);

/** A world-extent sentinel string or array; federation.md §1's explicit "no viewport" exception. */
const WORLD_EXTENT_STRING = "-180,-90,180,90";
const WORLD_EXTENT_ARRAY: readonly number[] = [-180, -90, 180, 90];

const ADMIN_CODE_PATTERN = /\bUS-(WA|OR|ID)\b/;
const REGION_WORD_PATTERN = /\b(Pacific Northwest|PNW)\b/;

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

/** True when `west/south/east/north` reads as a western-hemisphere WGS84 footprint, not a coincidence. */
function looksLikeFootprint(west: number, south: number, east: number, north: number): boolean {
  if (!Number.isFinite(west) || !Number.isFinite(south) || !Number.isFinite(east) || !Number.isFinite(north)) {
    return false;
  }
  if (west < -180 || west > 180 || east < -180 || east > 180) return false;
  if (south < -90 || south > 90 || north < -90 || north > 90) return false;
  if (!(west < east && south < north)) return false;
  // Every real regional/source footprint literal this codebase has ever declared sits entirely in
  // the western hemisphere (west AND east both negative); zoom-tier ladders like [0, 5, 9, 13] and
  // RGBA/index arrays that happen to have four in-range numbers do not, so this is the
  // discriminator that keeps this scan from flooding on unrelated 4-element literals.
  return west < 0 && east < 0;
}

const BBOX_STRING_PATTERN = /"(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)"/g;
const BBOX_ARRAY_PATTERN = /\[\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\]/g;
const BBOX_OBJECT_PATTERN =
  /\{\s*west:\s*(-?\d+(?:\.\d+)?)\s*,\s*south:\s*(-?\d+(?:\.\d+)?)\s*,\s*east:\s*(-?\d+(?:\.\d+)?)\s*,\s*north:\s*(-?\d+(?:\.\d+)?)\s*\}/g;
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

  for (const pattern of [BBOX_STRING_PATTERN, BBOX_ARRAY_PATTERN, BBOX_OBJECT_PATTERN]) {
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
      if (looksLikeFootprint(west, south, east, north)) {
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
 * Known offenders, enumerated by `path:line` description, migrated per federation.md §1 (moved
 * "into or behind the manifest") and §5 step 2b (`coverage-region.ts`'s non-PNW rows, staying
 * literal "until a future multi-region manifest registry exists to read them from" per
 * `src/lib/region/AGENTS.md` "Remaining footprint literals `coverage-region.ts` still carries").
 * Removing an entry once its value reads from `getRegion()` is the only edit this set may receive;
 * a new offender elsewhere fails this test rather than growing this list silently.
 */
const KNOWN_OFFENDERS = new Set<string>([
  // `coverage-region.ts`'s "California"/"Western United States"/"North America" rows describe
  // footprints this deployment does not serve; there is no manifest entry to point them at yet.
  "lib/map/coverage-region.ts:25:footprint literal { west: -125, south: 32, east: -114, north: 42.5 }",
  "lib/map/coverage-region.ts:26:footprint literal { west: -126, south: 31, east: -102, north: 50 }",
  "lib/map/coverage-region.ts:27:footprint literal { west: -170, south: 14, east: -52, north: 72 }",
  // The offline-download default bbox is its own narrower footprint (south 45/east -116, not the
  // manifest's 42/-111); reading `getRegion().defaultCameraEnvelope` here would silently widen the
  // pre-existing default download area, which this guard is not authorised to do. Its sibling
  // fallback three lines below (`{ west: w || -125, south: s || 45, ... }`) is not a literal this
  // pattern matches -- the value after each key is a `||` expression, not a bare number -- so it
  // is not (and should not become) a second entry here.
  "components/panels/OfflinePanel.tsx:52:footprint literal { west: -125, south: 45, east: -116, north: 49 }",
]);

function offenderKey(offender: FoundOffender): string {
  return `${offender.path}:${offender.line}:${offender.description}`;
}

describe("stray footprint literal guard", () => {
  it("finds no footprint/admin-code/region-name literal outside the manifest or KNOWN_OFFENDERS", () => {
    const files = listSourceFiles(SRC_ROOT);
    const found = new Set<string>();
    for (const filePath of files) {
      const relativePath = relative(SRC_ROOT, filePath).split(sep).join("/");
      if (ALLOWED_RELATIVE_PATHS.has(relativePath)) continue;
      const source = readFileSync(filePath, "utf8");
      for (const offender of scanFile(relativePath, source)) {
        found.add(offenderKey(offender));
      }
    }

    const newOffenders = [...found].filter((key) => !KNOWN_OFFENDERS.has(key));
    expect(
      newOffenders,
      "New footprint/admin-code/region-name literal(s) found outside the region manifest " +
        "(federation.md §1). Either read the value from `getRegion()` in a behaviour-neutral " +
        "one-line change, or add it to KNOWN_OFFENDERS with a citation."
    ).toEqual([]);

    const staleKnownOffenders = [...KNOWN_OFFENDERS].filter((key) => !found.has(key));
    expect(
      staleKnownOffenders,
      "KNOWN_OFFENDERS lists an entry this scan no longer finds; remove the stale entry rather " +
        "than let the debt list grow unverifiable."
    ).toEqual([]);
  });
});
