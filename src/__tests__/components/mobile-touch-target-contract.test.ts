import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

/**
 * The 44px mobile tap-target floor, enforced against the source rather than a rendered tree.
 *
 * `MapManagerMobileLayout.test.tsx` renders three dock sections and asserts the floor on the
 * specific controls those renders expose. That is the right test for the controls it names, and
 * it is why the dock's chrome held the floor for two months -- but it could only ever cover
 * controls reachable from a mocked state, which is how the dock's BODIES came to ship without
 * it. On 2026-10-10 a grep for `max-sm:` across all 33 files of `src/components/panels/`
 * returned zero, and no render test failed, because no render test knew those controls existed.
 *
 * So this suite inverts the question. Instead of "does this control I thought of carry the
 * floor", it asks "does every control in these directories carry the floor" -- which fails when
 * someone adds a NEW raw `<button>` tomorrow, the regression a render test structurally cannot
 * catch. It is a source scan because the invariant IS a source fact: a Tailwind class present in
 * a `className`. Nothing is mocked, nothing is mounted, and adding a panel costs no harness.
 *
 * Shared primitives are exempt BY their base class, not by an exclusion list: `<Button>`,
 * `<Input>`, `<Select>` and `<TabsTrigger>` are not raw elements, so they never match the scan,
 * and `touchTargetBearingPrimitives` below pins that their bases really do carry the floor. If
 * one of them loses it, that assertion fails rather than every call site silently going quiet.
 */

const REPO_ROOT = path.resolve(__dirname, "../../..");

/**
 * The directories this pass covered. Deliberately a list rather than all of `src/components`:
 * a scope the team has actually walked can be asserted at full strength, whereas a scope that
 * also swept un-walked trees would have to be weakened with exemptions to stay green, and a
 * weakened contract is the thing this file exists to replace.
 *
 * Widening it is the intended way to extend the pass -- add a glob, run the suite, fix what it
 * names.
 */
const SCANNED_DIRECTORIES = [
  "src/components/panels",
  "src/components/dashboard",
  "src/components/charts",
  "src/components/map/layer-panel",
];

const SCANNED_FILES = [
  "src/components/ui/tabs.tsx",
  "src/components/ui/button.tsx",
  "src/components/ui/input.tsx",
  "src/components/ui/select.tsx",
];

/**
 * Raw elements a thumb can hit. `<a>` is deliberately absent: a link inside a paragraph of prose
 * is not a tap target in the sense this floor is about, and including it would force an exemption
 * on every citation and inline reference -- exemptions being how a contract rots.
 */
/**
 * `<textarea>` is absent on purpose. Its HTML default is `rows=2` (HTML Standard, the textarea
 * element), so even an undecorated one is two lines plus padding -- comfortably past 44px -- and
 * the floor on it is always a no-op. It still needs `NO_IOS_FOCUS_ZOOM`, which is a font-size
 * concern and is asserted separately below, not a size one.
 */
const INTERACTIVE_TAG = /<(button|input|select)[\s>]/g;
const INTERACTIVE_ROLE = /role=["'](switch|tab|checkbox|radio|menuitem|option)["']/g;

/**
 * What counts as satisfying the floor.
 *
 * Both the `max-sm:`-gated form (the contract in `touch-target.ts`) and an unconditional
 * `min-h-11`/`h-11`/`h-12` count: a control that is already 44px on every viewport has nothing
 * to fix on a phone, and demanding it be rewritten into the gated form would be churn that
 * lowers desktop targets for no gain. `TOUCH_TARGET` matches the interpolated constant.
 */
/**
 * Token boundaries are `(?<![\w:-])` / `(?![\w-])`, not `\b`, and that distinction matters.
 *
 * `\b` sits happily after a hyphen or a colon, so the first cut of this pattern accepted
 * `max-h-11` -- a MAXIMUM height, the opposite claim -- and `data-testid="size-11-probe"` as
 * floors. Excluding `-` and `:` on the left kills both, which is exactly why the `max-sm:`
 * forms must be spelled out as their own alternative: their own colon would be rejected too.
 */
const FLOOR =
  /TOUCH_TARGET|max-sm:(?:min-h|h|size)-1[12](?![\w-])|(?<![\w:-])(?:min-h|h|size)-1[12](?![\w-])/;

/**
 * Controls that are genuinely not tap targets, each needing a stated reason in the source.
 *
 * `sr-only` and `hidden` are visually absent, so their size is not a target; a native
 * checkbox/radio styled `sr-only` is driven by a `<label>` that carries the floor itself.
 * `type="hidden"` carries no box at all. Anything else must say why in a marker comment, which
 * keeps an exemption reviewable in the diff that introduces it instead of accruing silently.
 */
/**
 * The left boundary excludes `:` as well as `-`, and that is load-bearing in a way `\b` was not:
 * `\bhidden\b` matched `overflow-hidden`, `aria-hidden` AND `sm:hidden` -- and that last one
 * INVERTS the contract, auto-exempting a control that is hidden on the desktop and visible only
 * on a phone. Only an unprefixed `hidden` means "this element has no box".
 */
const EXEMPT =
  /(?<![\w:-])(?:sr-only|hidden)(?![\w-])|type=["'](?:hidden|checkbox|radio)["']|touch-target-exempt/;

/**
 * Why `checkbox` and `radio` are exempt, rather than merely small.
 *
 * Their tap surface is the `<label>` that wraps them -- a press anywhere on it toggles the
 * control natively -- so the floor belongs on that label, which carries the text and the padding
 * that make it large in the first place. Sizing the box itself paints a 44px checkbox, and on
 * 2026-10-10 that is exactly what an earlier run of this suite caused: an author added
 * `TOUCH_TARGET_SQUARE` to three consent checkboxes and said so in their report -- "the
 * surrounding label already gives a large native tap area, but I still marked the checkbox
 * itself since the contract scanner treats every raw input as a target". The scanner was
 * steering the UI, which is a scanner bug.
 *
 * A regex cannot prove the enclosing label is big enough, so this is a deliberate blind spot
 * rather than a false claim of coverage: the rule is stated in `touch-target.ts` and in
 * `panels/AGENTS.md`, and the label is reviewed by eye. A check that forces a wrong fix is worse
 * than one with an honest gap.
 */

/**
 * How far back of the RAW source to read for an exemption marker.
 *
 * The marker is a comment, and a comment sits BEFORE the tag it explains rather than inside it,
 * so the tag text alone can never carry one. `stripComments` blanks each comment to a run of
 * spaces of the SAME length, which is precisely what keeps an offset into the stripped source
 * valid against the raw source -- so this window can be read out of `raw` at the same index.
 * One window is generous enough for a four-line rationale and short enough not to reach back
 * into the previous control and exempt it by accident.
 */
const EXEMPTION_LOOKBEHIND = 500;

/**
 * Cross-file helpers whose own definition carries the floor, so `className={helper(...)}` counts.
 *
 * Only for helpers defined in ANOTHER module -- `collectFlooredIdentifiers` below resolves
 * same-file ones on its own, which is the common case and the one worth not hand-maintaining.
 * `buttonVariants` is the cva that `ui/button.tsx` builds its own `<button>` from; the floor is
 * in its base string, which the dedicated primitive assertion below pins separately.
 */
const FLOOR_BEARING_HELPERS = [
  "dockDisclosureRowClasses",
  "pickerButtonClassName",
  "buttonVariants",
] as const;

/**
 * The opening tag starting at `start`, as source text.
 *
 * Scans to the first `>` that sits outside quotes and outside a JSX expression container, since
 * an arbitrary-value background-image utility whose inlined SVG contains a greater-than sign
 * (as `ui/select.tsx`'s chevron does), or a ternary holding a nested generic, would otherwise
 * truncate the tag halfway and hide the rest of its attributes from the scan.
 */
function readOpeningTag(source: string, start: number): string {
  let depth = 0;
  let quote: string | null = null;
  for (let i = start; i < source.length; i++) {
    const char = source[i];
    if (quote !== null) {
      if (char === quote) quote = null;
      continue;
    }
    if (char === '"' || char === "'" || char === "`") {
      quote = char;
      continue;
    }
    if (char === "{") depth++;
    else if (char === "}") depth--;
    else if (char === ">" && depth === 0) return source.slice(start, i + 1);
  }
  return source.slice(start);
}

/** `source` with every comment blanked, so a `<button>` inside a doc comment is not a finding. */
function stripComments(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, (match) => " ".repeat(match.length))
    .replace(/(^|[^:])\/\/[^\n]*/g, (match) => " ".repeat(match.length));
}

interface Finding {
  file: string;
  line: number;
  snippet: string;
}

/**
 * The names declared in this file whose class string carries the floor, transitively.
 *
 * Authors factor a repeated class list into a local constant -- `DISCLOSURE_BUTTON` for the AI
 * report's six controls, `inputClass`/`selectClass` for a settings form's fields, `item` for a
 * menu's rows -- and then write `className={`${DISCLOSURE_BUTTON} …`}`. The floor is genuinely
 * there, one hop away, but the tag text never contains it. Resolving those names is what keeps
 * this suite from punishing the better-factored file and pushing authors to paste the class six
 * times instead.
 *
 * Transitive by fixpoint, because the hops compose: `item` carries no floor of its own and
 * interpolates `DISCLOSURE_BUTTON`, which does. Iterating until the set stops growing resolves
 * that chain whatever order the declarations appear in.
 *
 * It cannot follow an import, which is what `FLOOR_BEARING_HELPERS` is for.
 */
function collectFlooredIdentifiers(source: string): Set<string> {
  const declarations = new Map<string, string>();
  // A declaration's value runs to the end of its statement. `[^;]*` is coarse but safe here: a
  // class list is a string or a template, and neither contains a semicolon in this codebase.
  for (const match of source.matchAll(/\b(?:const|let)\s+([A-Za-z_$][\w$]*)\s*=\s*([^;]*);/g)) {
    declarations.set(match[1], match[2]);
  }

  const floored = new Set<string>();
  for (let pass = 0; pass < declarations.size + 1; pass++) {
    let grew = false;
    for (const [name, value] of declarations) {
      if (floored.has(name)) continue;
      const carriesFloor =
        FLOOR.test(value) ||
        [...floored].some((known) => new RegExp(`\\b${known}\\b`).test(value)) ||
        FLOOR_BEARING_HELPERS.some((helper) => value.includes(helper));
      if (carriesFloor) {
        floored.add(name);
        grew = true;
      }
    }
    if (!grew) break;
  }
  return floored;
}

function scanFile(absolutePath: string): Finding[] {
  const raw = readFileSync(absolutePath, "utf8");
  const source = stripComments(raw);
  const relative = path.relative(REPO_ROOT, absolutePath).replace(/\\/g, "/");
  const findings: Finding[] = [];
  const flooredIdentifiers = collectFlooredIdentifiers(source);

  const starts: number[] = [];
  for (const match of source.matchAll(INTERACTIVE_TAG)) starts.push(match.index);
  // A `role="switch"` sits on whatever element implements it, so walk back to that element's
  // own `<`; the tag is then read forward from there like any other.
  for (const match of source.matchAll(INTERACTIVE_ROLE)) {
    const tagStart = source.lastIndexOf("<", match.index);
    if (tagStart !== -1) starts.push(tagStart);
  }

  for (const start of [...new Set(starts)].sort((a, b) => a - b)) {
    const tag = readOpeningTag(source, start);
    // `querySelectorAll<HTMLButtonElement>("[role='tab']")` walks back to a TYPE parameter, not
    // to an element. Only a lowercase tag name is JSX markup; `<HTMLButtonElement>` is not.
    if (!/^<[a-z]/.test(tag)) continue;
    // An unterminated tag means the quote/brace scan lost track -- an attribute such as
    // {`${name}'s layers`} opens a quote state that never closes, so the "tag" becomes the rest
    // of the file, which almost certainly contains the word TOUCH_TARGET somewhere and would
    // therefore PASS. A parse failure has to be loud rather than silently green.
    if (!tag.endsWith(">")) {
      findings.push({
        file: relative,
        line: source.slice(0, start).split("\n").length,
        snippet: `UNPARSEABLE opening tag (no '>' found): ${tag.replace(/\s+/g, " ").slice(0, 120)}`,
      });
      continue;
    }
    if (FLOOR.test(tag) || EXEMPT.test(tag)) continue;
    const precedingSource = raw.slice(Math.max(0, start - EXEMPTION_LOOKBEHIND), start);
    if (precedingSource.includes("touch-target-exempt")) continue;
    // Word-bounded: a local constant may be named `item`, which must not be satisfied by the
    // word "items" appearing somewhere in an unrelated attribute.
    if ([...flooredIdentifiers].some((name) => new RegExp(`\\b${name}\\b`).test(tag))) continue;
    if (FLOOR_BEARING_HELPERS.some((helper) => tag.includes(helper))) continue;
    findings.push({
      file: relative,
      line: source.slice(0, start).split("\n").length,
      snippet: tag.replace(/\s+/g, " ").slice(0, 160),
    });
  }
  return findings;
}

/**
 * Every `.tsx` under `directory`, recursively, with tests excluded.
 *
 * Hand-rolled rather than globbed because `fast-glob` resolves in this repo only as a transitive
 * dependency of the toolchain -- it is absent from `package.json`, so a lockfile change could
 * delete it from under this suite and the failure would read as a broken test rather than a
 * missing dep.
 */
function collectTsxFiles(directory: string): string[] {
  const entries = readdirSync(directory, { withFileTypes: true });
  return entries.flatMap((entry) => {
    const absolute = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      return entry.name === "__tests__" ? [] : collectTsxFiles(absolute);
    }
    return entry.isFile() && entry.name.endsWith(".tsx") ? [absolute] : [];
  });
}

describe("mobile tap-target floor (source contract)", () => {
  const files = [
    ...SCANNED_DIRECTORIES.flatMap((relative) =>
      collectTsxFiles(path.join(REPO_ROOT, relative))
    ),
    ...SCANNED_FILES.map((relative) => path.join(REPO_ROOT, relative)),
  ];

  it("scans a non-empty file set", () => {
    // Without this, a broken glob or a moved directory turns the suite below into a test that
    // asserts nothing and passes -- the exact failure mode that let the panels drift.
    expect(files.length).toBeGreaterThan(30);
  });

  it("gives every raw interactive control the 44px floor or a stated exemption", () => {
    const findings = files.flatMap(scanFile);
    const report = findings
      .map((finding) => `  ${finding.file}:${finding.line}\n    ${finding.snippet}`)
      .join("\n");

    expect(
      findings,
      findings.length === 0
        ? ""
        : `${findings.length} interactive control(s) are missing the mobile tap-target floor.\n` +
            `Import TOUCH_TARGET (or TOUCH_TARGET_SQUARE for icon-only controls) from ` +
            `"@/components/ui/touch-target" and interpolate it into the className, or mark the ` +
            `control /* touch-target-exempt: <reason> */ if it is genuinely not a tap target.\n` +
            `See src/components/ui/touch-target.ts.\n${report}`
    ).toEqual([]);
  });

  it("keeps the floor in the shared primitives the call sites rely on", () => {
    // Every <Button>/<Input>/<Select>/<TabsTrigger> in the app inherits the floor from these
    // four base class strings and adds nothing of its own, so the scan above cannot see them.
    // If one of these loses the floor, hundreds of call sites regress in silence -- unless this
    // fails first.
    const touchTargetBearingPrimitives = [
      "src/components/ui/button.tsx",
      "src/components/ui/input.tsx",
      "src/components/ui/select.tsx",
      "src/components/ui/tabs.tsx",
    ];
    for (const relative of touchTargetBearingPrimitives) {
      const source = readFileSync(path.join(REPO_ROOT, relative), "utf8");
      expect(source, `${relative} must carry the mobile floor in its base classes`).toMatch(
        /TOUCH_TARGET/
      );
    }
  });

  it("raises native fields to 16px on phones so mobile Safari does not zoom the page", () => {
    // Focus on a field under 16px zooms the whole document in mobile Safari and does not zoom
    // back out on blur. On a full-bleed map that strands the reader pinch-zoomed into a corner
    // with the dock off screen, so the three shared field primitives and the layer time
    // slider's date input all carry NO_IOS_FOCUS_ZOOM. This is the one font size the pass
    // raises; see the rationale in touch-target.ts.
    const fieldSources = [
      "src/components/ui/input.tsx",
      "src/components/ui/select.tsx",
      "src/components/map/layer-panel/LayerTimeSlider.tsx",
    ];
    for (const relative of fieldSources) {
      const source = readFileSync(path.join(REPO_ROOT, relative), "utf8");
      expect(source, `${relative} must carry NO_IOS_FOCUS_ZOOM on its native field`).toMatch(
        /NO_IOS_FOCUS_ZOOM/
      );
    }
  });
});
