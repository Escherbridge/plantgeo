/**
 * Guard: no `getRegion()` call may run while a module under `src/` is being evaluated.
 *
 * `federation.md` §1 -- "the manifest is read at ingress once and passed down ... they do not
 * import a module-level constant that hides the dependency" -- plus the standing
 * manifest-moves-must-be-lazy rule. A module-scope read snapshots whichever region resolved first
 * (so a second region is served the first one's footprint) and, since `getRegion()` began refusing
 * an unregistered slug, throws during module evaluation and takes down every importer rather than
 * one render. `coverage-region.ts` carried exactly that shape for two waves after the doc that
 * cited it as fixed (STYLE-REVIEW-W8 S2), which is why this is a mechanism and not a sentence.
 *
 * Its Python twin is `services/agri-data-service/tests/test_module_scope_region_read.py`, which
 * asks the same question of `load_region()` with `ast`.
 */

import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const SOURCE_ROOT = path.resolve(__dirname, "..", "..");
const SOURCE_EXTENSIONS = new Set([".ts", ".tsx"]);
const REGION_READER_CALL = "getRegion(";

/** Directories whose contents are test scaffolding rather than shipped modules. */
const SKIPPED_DIRECTORY_NAMES = new Set(["__tests__", "__benchmarks__", "test", "node_modules"]);

function sourceFilesUnder(directory: string): string[] {
  const found: string[] = [];
  for (const entry of readdirSync(directory)) {
    const absolutePath = path.join(directory, entry);
    if (statSync(absolutePath).isDirectory()) {
      if (SKIPPED_DIRECTORY_NAMES.has(entry)) continue;
      found.push(...sourceFilesUnder(absolutePath));
      continue;
    }
    if (entry.includes(".test.")) continue;
    if (SOURCE_EXTENSIONS.has(path.extname(entry))) found.push(absolutePath);
  }
  return found;
}

/**
 * Blanks comments and string/template bodies so a `getRegion()` spelled inside prose or a message
 * is not a finding. Every replaced character keeps its offset, so line numbers stay exact.
 */
function withoutCommentsAndStrings(source: string): string {
  const blanked = source.split("");
  let index = 0;
  const blankUntil = (end: number) => {
    for (let cursor = index; cursor < end && cursor < blanked.length; cursor += 1) {
      if (blanked[cursor] !== "\n") blanked[cursor] = " ";
    }
  };
  while (index < source.length) {
    const character = source[index];
    const nextCharacter = source[index + 1];
    if (character === "/" && nextCharacter === "/") {
      const end = source.indexOf("\n", index);
      const stop = end === -1 ? source.length : end;
      blankUntil(stop);
      index = stop;
      continue;
    }
    if (character === "/" && nextCharacter === "*") {
      const end = source.indexOf("*/", index + 2);
      const stop = end === -1 ? source.length : end + 2;
      blankUntil(stop);
      index = stop;
      continue;
    }
    if (character === '"' || character === "'" || character === "`") {
      let cursor = index + 1;
      while (cursor < source.length) {
        if (source[cursor] === "\\") {
          cursor += 2;
          continue;
        }
        if (source[cursor] === character) break;
        cursor += 1;
      }
      const stop = Math.min(cursor + 1, source.length);
      blankUntil(stop);
      index = stop;
      continue;
    }
    index += 1;
  }
  return blanked.join("");
}

/**
 * Every `getRegion(` that is reached while the module body runs, as `file:line`.
 *
 * Nesting depth alone would clear a top-level `const camera = getRegion().x` only by accident, so
 * the walk counts brackets AND remembers whether the current top-level statement has already
 * opened a function body: after a `function` keyword or a `=>`, the call is deferred to a caller
 * and is exactly the shape this rule asks for. `{`/`(`/`[` depth returning to zero starts a new
 * statement and clears that memory.
 */
function moduleScopeRegionReads(source: string): number[] {
  const code = withoutCommentsAndStrings(source);
  const lines = code.split("\n");
  let depth = 0;
  let deferredInThisStatement = false;
  const offenderLines: number[] = [];
  for (let lineIndex = 0; lineIndex < lines.length; lineIndex += 1) {
    const line = lines[lineIndex];
    for (let column = 0; column < line.length; column += 1) {
      const character = line[column];
      if (character === "{" || character === "(" || character === "[") depth += 1;
      else if (character === "}" || character === ")" || character === "]") {
        depth = Math.max(0, depth - 1);
        // A top-level body just closed, so the next statement starts with no deferral in hand --
        // without this a single `function` declaration would excuse every read below it.
        if (depth === 0 && character === "}") deferredInThisStatement = false;
      } else if (character === ";" && depth === 0) deferredInThisStatement = false;
      if (line.startsWith("=>", column) || /^function\b/.test(line.slice(column))) {
        deferredInThisStatement = true;
      }
      if (
        line.startsWith(REGION_READER_CALL, column) &&
        !/[\w$.]/.test(line[column - 1] ?? "") &&
        depth === 0 &&
        !deferredInThisStatement
      ) {
        offenderLines.push(lineIndex + 1);
      }
    }
  }
  return offenderLines;
}

describe("region manifest reads under src/", () => {
  it("never calls getRegion() while a module body is evaluating", () => {
    const offenders: string[] = [];
    for (const absolutePath of sourceFilesUnder(SOURCE_ROOT)) {
      const source = readFileSync(absolutePath, "utf8");
      if (!source.includes(REGION_READER_CALL)) continue;
      for (const line of moduleScopeRegionReads(source)) {
        offenders.push(`${path.relative(SOURCE_ROOT, absolutePath).replaceAll("\\", "/")}:${line}`);
      }
    }
    expect(
      offenders,
      "a module-scope getRegion() snapshots the first region resolved and throws at import for an " +
        "unregistered slug; move the read inside the function that needs it, or take a Region " +
        "parameter (federation.md §1)"
    ).toEqual([]);
  });

  it("still recognises the shape it is guarding against", () => {
    expect(moduleScopeRegionReads("export const BOX = getRegion().envelope;\n")).toEqual([1]);
    expect(moduleScopeRegionReads("export function box() {\n  return getRegion().envelope;\n}\n")).toEqual([]);
    expect(moduleScopeRegionReads("const box = () => getRegion().envelope;\n")).toEqual([]);
    expect(moduleScopeRegionReads("// getRegion() in a comment\n")).toEqual([]);
    expect(moduleScopeRegionReads('const note = "getRegion() in a string";\n')).toEqual([]);
  });
});
