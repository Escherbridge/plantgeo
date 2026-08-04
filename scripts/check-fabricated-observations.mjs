import { readdir, readFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";

/**
 * Fails the build when a browser-visible module can synthesise an observation.
 *
 * A fabricated point in a time slider is the worst defect this codebase can ship: it is
 * indistinguishable from a measured one, it carries the date the user asked for, and the map
 * renders it with full confidence. A module that shipped exactly that lived in src/stores for
 * a while behind `process.env.NODE_ENV !== "production"`, which is not a defence -- dev, test
 * and every preview deploy run outside production, and that is where the map gets looked at.
 *
 * Observations reach the client over the wire from src/lib/server or not at all, so the two
 * fingerprints of a hand-built MetricAtDateProperties are banned outside the server tree.
 * The patterns require a value terminator, so the type declarations in src/types (where the
 * literal is followed by ` |`) are not matched.
 */
const DISPLAY_ROOTS = [
  "src/app",
  "src/components",
  "src/hooks",
  "src/lib",
  "src/stores",
  "src/types",
  "src/workers",
];

const FABRICATION_PATTERNS = [
  {
    pattern: /\bavailability\s*:\s*(["'])published\1\s*[,})\]]/g,
    explanation:
      'claims "published" availability; only a server response may assert that data exists',
  },
  {
    pattern: /\bvalueKind\s*:\s*(["'])(?:observed|forecast)\1\s*[,})\]]/g,
    explanation:
      "builds a metric value literal; observed and forecast values come from the warehouse only",
  },
];

const SOURCE_EXTENSION = /\.(?:[cm]?[jt]sx?)$/;

async function collectSourceFiles(relativeDirectory) {
  const absoluteDirectory = path.resolve(relativeDirectory);
  const entries = await readdir(absoluteDirectory, { withFileTypes: true });
  const files = [];

  for (const entry of entries) {
    const absolutePath = path.join(absoluteDirectory, entry.name);
    const relativePath = path.relative(process.cwd(), absolutePath).replaceAll("\\", "/");
    if (entry.isDirectory()) {
      // The server tree is the only legitimate origin of an observation; tests assert on
      // fixtures by design and never ship to a browser.
      if (relativePath.startsWith("src/lib/server/") || relativePath.includes("/__tests__/")) {
        continue;
      }
      files.push(...(await collectSourceFiles(relativePath)));
    } else if (SOURCE_EXTENSION.test(entry.name) && !entry.name.includes(".test.")) {
      files.push({ absolutePath, relativePath });
    }
  }
  return files;
}

const violations = [];
for (const root of DISPLAY_ROOTS) {
  for (const file of await collectSourceFiles(root)) {
    const source = await readFile(file.absolutePath, "utf8");
    for (const rule of FABRICATION_PATTERNS) {
      rule.pattern.lastIndex = 0;
      for (const match of source.matchAll(rule.pattern)) {
        const line = source.slice(0, match.index).split("\n").length;
        violations.push(`${file.relativePath}:${line} ${rule.explanation}`);
      }
    }
  }
}

if (violations.length > 0) {
  console.error(
    "Client module(s) can synthesise an observation. A fabricated point is indistinguishable " +
      "from a measured one on a map; a visible gap is the correct outcome:\n" +
      violations.map((violation) => `- ${violation}`).join("\n"),
  );
  process.exitCode = 1;
} else {
  console.log(
    `Observation-fabrication check passed (${FABRICATION_PATTERNS.length} rules over ${DISPLAY_ROOTS.length} display roots).`,
  );
}
