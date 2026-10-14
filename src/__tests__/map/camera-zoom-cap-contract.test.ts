import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { MAP_MAX_ZOOM } from "@/lib/map/camera-limits";
import { styles } from "@/lib/map/styles";

/**
 * The map's zoom ceiling, enforced against the source rather than a rendered tree.
 *
 * `MapView.tsx` and `EmbedMap.tsx` used to construct `maplibregl.Map` with no `maxZoom` at
 * all, so the camera could reach MapLibre's own default of z22 while even the deepest source
 * this platform serves (the satellite raster, z19 -- see camera-limits.ts) had nothing left
 * to paint but a stretched crop of its last real tile. A mobile pinch gesture overshoots that
 * boundary far more easily than a mouse wheel ever does, which is how "data granularity
 * breaks on mobile" (2026-10-10) happened without any single call site looking wrong on its
 * own -- the camera itself had no ceiling.
 *
 * Mirrors `mobile-touch-target-contract.test.ts`'s approach for the same reason that file
 * gives: a render test only covers the Map instance or the camera call it was written
 * against, so it cannot catch a NEW one added tomorrow without a ceiling. This is a source
 * scan because the invariant IS a source fact -- does this construction or this call mention
 * the shared ceiling (or a stated reason it does not need to).
 */

const REPO_ROOT = path.resolve(__dirname, "../../..");

/**
 * Every place a MapLibre camera is built or moved in this codebase, confirmed by grepping
 * `src/components/map`, `src/components/embed`, `src/components/panels` and `src/lib/map`
 * before writing this list (see the recon in conductor/tracks or the mobile-max-zoom lane
 * report). Widening it is the intended way to extend the pass -- add a directory, run the
 * suite, fix what it names.
 */
const SCANNED_DIRECTORIES = [
  "src/components/map",
  "src/components/embed",
  "src/components/panels",
  "src/lib/map",
];

const MAP_CONSTRUCTOR = /new\s+maplibregl\.Map\s*\(/g;
const CAMERA_MOVE = /\.(flyTo|fitBounds|easeTo|jumpTo|setZoom|cameraForBounds)\s*\(/g;

/** What counts as "this construction/call names the shared ceiling". */
const CAP_REFERENCE = /\bMAP_MAX_ZOOM\b|\bclampCameraZoom\s*\(/;

/** A `maxZoom` constructor option -- required to come FROM the constant, not a bare literal. */
const MAX_ZOOM_FROM_CONSTANT = /\bmaxZoom\s*:\s*MAP_MAX_ZOOM\b/;

/**
 * Explicit exemption marker, exactly like `touch-target-exempt` in the mobile tap-target
 * contract: a comment stating why this one construction or call deliberately carries no cap
 * (e.g. its zoom/center is already bounded upstream by a shared contract both ends obey --
 * see `MapFocus.tsx`'s `jumpTo`, whose `focus.zoom` is clamped by `readMapFocus`'s
 * `clampCameraZoom` call in `focus-params.ts`).
 */
const EXEMPTION_MARKER = "camera-cap-exempt";

/** How far back of the raw source to read for an exemption marker or a declaration site. */
const LOOKBEHIND = 400;

/** `source` with every comment blanked, so a mention of these patterns in prose is not a finding. */
function stripComments(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, (match) => " ".repeat(match.length))
    .replace(/(^|[^:])\/\/[^\n]*/g, (match) => " ".repeat(match.length));
}

/**
 * The balanced `(...)` region starting at `openIndex` (which must point at the opening paren),
 * quote-aware so a string argument containing an unmatched paren cannot desync the depth count.
 * Mirrors `readOpeningTag` in the touch-target contract, generalised to any single delimiter
 * pair -- the method name already fixes which pair is being balanced here.
 */
function extractBalancedParens(source: string, openIndex: number): string {
  let depth = 0;
  let quote: string | null = null;
  for (let i = openIndex; i < source.length; i++) {
    const char = source[i];
    if (quote !== null) {
      if (char === quote && source[i - 1] !== "\\") quote = null;
      continue;
    }
    if (char === '"' || char === "'" || char === "`") {
      quote = char;
      continue;
    }
    if (char === "(") depth++;
    else if (char === ")") {
      depth--;
      if (depth === 0) return source.slice(openIndex, i + 1);
    }
  }
  return source.slice(openIndex);
}

interface Finding {
  file: string;
  line: number;
  snippet: string;
}

function lineOf(source: string, index: number): number {
  return source.slice(0, index).split("\n").length;
}

function hasExemption(raw: string, matchStart: number): boolean {
  return raw.slice(Math.max(0, matchStart - LOOKBEHIND), matchStart).includes(EXEMPTION_MARKER);
}

/**
 * Checks every `new maplibregl.Map(...)` construction: it must set `maxZoom` FROM the shared
 * constant (a bare literal such as `maxZoom: 20` does not count -- the constant is the point,
 * since a hand-picked number drifts the moment the real ceiling changes), or carry the
 * exemption marker.
 */
function scanConstructors(raw: string, source: string, relative: string): Finding[] {
  const findings: Finding[] = [];
  for (const match of source.matchAll(MAP_CONSTRUCTOR)) {
    const openIndex = match.index + match[0].length - 1;
    const region = extractBalancedParens(source, openIndex);
    if (MAX_ZOOM_FROM_CONSTANT.test(region)) continue;
    if (hasExemption(raw, match.index)) continue;
    findings.push({
      file: relative,
      line: lineOf(source, match.index),
      snippet: region.replace(/\s+/g, " ").slice(0, 160),
    });
  }
  return findings;
}

/**
 * Checks every camera-move call. A call whose argument region contains neither `{` (so it is
 * either a bare identifier/expression this scan cannot see into, e.g. `map.jumpTo(camera)`, or
 * a bare number such as `setZoom(9)`) is treated as OPAQUE and always needs a cap reference or
 * an exemption -- it is exactly the shape that let `MapFocus.tsx`'s `jumpTo(camera)` hide an
 * uncapped zoom behind a local variable. A call whose argument region DOES show its object
 * literal is only flagged when that literal actually sets `zoom` or `center` -- a padding- or
 * pitch-only `easeTo`/`jumpTo` (see `LayerPanel.tsx`'s panel-padding effect, or `MapView.tsx`'s
 * pitch toggle) never moves the camera's zoom at all and has nothing to cap.
 */
function scanCameraMoves(raw: string, source: string, relative: string): Finding[] {
  const findings: Finding[] = [];
  for (const match of source.matchAll(CAMERA_MOVE)) {
    const openIndex = match.index + match[0].length - 1;
    const region = extractBalancedParens(source, openIndex);
    if (CAP_REFERENCE.test(region)) continue;
    if (hasExemption(raw, match.index)) continue;

    const inner = region.slice(1, -1).trim();
    const isOpaque = !inner.includes("{");
    const movesZoomOrCenter = /\bzoom\s*:|\bcenter\s*:/.test(inner);
    if (!isOpaque && !movesZoomOrCenter) continue;

    findings.push({
      file: relative,
      line: lineOf(source, match.index),
      snippet: `${match[0]}${region.slice(1).replace(/\s+/g, " ").slice(0, 140)}`,
    });
  }
  return findings;
}

function scanFile(absolutePath: string): Finding[] {
  const raw = readFileSync(absolutePath, "utf8");
  const source = stripComments(raw);
  const relative = path.relative(REPO_ROOT, absolutePath).replace(/\\/g, "/");
  return [...scanConstructors(raw, source, relative), ...scanCameraMoves(raw, source, relative)];
}

/** Every `.ts`/`.tsx` under `directory`, recursively, with tests excluded. */
function collectSourceFiles(directory: string): string[] {
  const entries = readdirSync(directory, { withFileTypes: true });
  return entries.flatMap((entry) => {
    const absolute = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      return entry.name === "__tests__" ? [] : collectSourceFiles(absolute);
    }
    return entry.isFile() && /\.tsx?$/.test(entry.name) ? [absolute] : [];
  });
}

describe("map camera zoom-cap contract (source scan)", () => {
  const files = SCANNED_DIRECTORIES.flatMap((relative) =>
    collectSourceFiles(path.join(REPO_ROOT, relative))
  );

  it("scans a non-empty file set", () => {
    // Without this, a moved directory turns the suite below into a test that asserts
    // nothing and passes -- the exact failure mode the touch-target contract's own
    // equivalent check guards against.
    expect(files.length).toBeGreaterThan(10);
  });

  it("caps every Map construction and camera move, or requires a stated exemption", () => {
    const findings = files.flatMap(scanFile);
    const report = findings
      .map((finding) => `  ${finding.file}:${finding.line}\n    ${finding.snippet}`)
      .join("\n");

    expect(
      findings,
      findings.length === 0
        ? ""
        : `${findings.length} Map construction(s) or camera move(s) are missing the shared ` +
            `zoom ceiling.\nGive a new "maplibregl.Map(...)" a "maxZoom: MAP_MAX_ZOOM" option, ` +
            `wrap a new/edited zoom value in "clampCameraZoom(...)", or mark the call ` +
            `/* camera-cap-exempt: <reason> */ if it is genuinely already bounded.\n` +
            `See src/lib/map/camera-limits.ts.\n${report}`
    ).toEqual([]);
  });
});

/**
 * The other half of the ceiling: what the camera can reach must still be painted. MapLibre HIDES a
 * layer at every zoom >= its `maxzoom`, so a layer `maxzoom` at or below MAP_MAX_ZOOM goes blank
 * before the camera stops -- the satellite imagery did exactly that at z19 (2026-10-13 review). A
 * layer meant to vanish below the ceiling is named in this allow-list with its reason; none is today.
 */
const LAYERS_HIDDEN_BELOW_THE_CEILING: ReadonlySet<string> = new Set<string>();

describe("every style paints at the camera ceiling", () => {
  it("gives no layer a maxzoom at or below MAP_MAX_ZOOM", () => {
    const hidden = Object.entries(styles).flatMap(([name, style]) =>
      style.layers
        .filter((layer) => {
          const { maxzoom } = layer as { maxzoom?: number };
          return maxzoom !== undefined && maxzoom <= MAP_MAX_ZOOM && !LAYERS_HIDDEN_BELOW_THE_CEILING.has(layer.id);
        })
        .map((layer) => `${name}/${layer.id} (maxzoom ${(layer as { maxzoom?: number }).maxzoom})`)
    );

    expect(hidden, `hidden at or below the z${MAP_MAX_ZOOM} camera ceiling: ${hidden.join(", ")}`).toEqual([]);
  });
});
