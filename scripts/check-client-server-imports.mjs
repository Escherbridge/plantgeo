import { readdir, readFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";

const ROOTS = ["src/app", "src/components", "src/hooks", "src/lib", "src/stores", "src/workers"];
const ALWAYS_BROWSER_ROOTS = ["src/components/", "src/hooks/", "src/stores/", "src/workers/"];
const SOURCE_EXTENSION = /\.(?:[cm]?[jt]sx?)$/;
const TYPE_ONLY_ALLOWLIST = new Set([
  "src/lib/trpc/client.ts:@/lib/server/trpc/router",
]);

async function collect(relativeDirectory) {
  const absoluteDirectory = path.resolve(relativeDirectory);
  const entries = await readdir(absoluteDirectory, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const absolutePath = path.join(absoluteDirectory, entry.name);
    const relativePath = path.relative(process.cwd(), absolutePath).replaceAll("\\", "/");
    if (entry.isDirectory()) {
      if (
        relativePath.startsWith("src/lib/server/") ||
        relativePath.includes("/__tests__/") ||
        relativePath.includes("/api/")
      ) {
        continue;
      }
      files.push(...(await collect(relativePath)));
    } else if (SOURCE_EXTENSION.test(entry.name) && !entry.name.includes(".test.")) {
      files.push({ absolutePath, relativePath });
    }
  }
  return files;
}

function isBrowserSurface(relativePath, source) {
  return (
    ALWAYS_BROWSER_ROOTS.some((root) => relativePath.startsWith(root)) ||
    /^\s*["']use client["'];/m.test(source)
  );
}

function lineNumber(source, offset) {
  return source.slice(0, offset).split("\n").length;
}

function isTypeOnlyClause(clause) {
  const trimmed = clause.trim();
  if (trimmed.startsWith("type ")) return true;
  if (!trimmed.startsWith("{") || !trimmed.endsWith("}")) return false;
  const imports = trimmed.slice(1, -1).split(",").map((value) => value.trim()).filter(Boolean);
  return imports.length > 0 && imports.every((value) => value.startsWith("type "));
}

const violations = [];
for (const root of ROOTS) {
  for (const file of await collect(root)) {
    const source = await readFile(file.absolutePath, "utf8");
    if (!isBrowserSurface(file.relativePath, source)) continue;

    const staticImports = /\bimport\s+([^;]*?)\s+from\s+["'](@\/lib\/server(?:\/[^"']*)?)["']/g;
    for (const match of source.matchAll(staticImports)) {
      const edge = `${file.relativePath}:${match[2]}`;
      if (!isTypeOnlyClause(match[1]) || !TYPE_ONLY_ALLOWLIST.has(edge)) {
        violations.push(`${file.relativePath}:${lineNumber(source, match.index)} ${match[2]}`);
      }
    }

    const sideEffectImports = /\bimport\s+["'](@\/lib\/server(?:\/[^"']*)?)["']/g;
    for (const match of source.matchAll(sideEffectImports)) {
      violations.push(`${file.relativePath}:${lineNumber(source, match.index)} ${match[1]}`);
    }

    const runtimeLoads = /(?:\bimport\s*\(|\brequire\s*\()\s*["'](@\/lib\/server(?:\/[^"']*)?)["']/g;
    for (const match of source.matchAll(runtimeLoads)) {
      violations.push(`${file.relativePath}:${lineNumber(source, match.index)} ${match[1]}`);
    }

    const runtimeExports = /\bexport\s+(?!type\b)(?:\{[^}]*\}|\*)\s+from\s+["'](@\/lib\/server(?:\/[^"']*)?)["']/g;
    for (const match of source.matchAll(runtimeExports)) {
      violations.push(`${file.relativePath}:${lineNumber(source, match.index)} ${match[1]}`);
    }
  }
}

if (violations.length > 0) {
  console.error(
    "Browser code must use client-safe contracts instead of server modules:\n" +
      violations.map((violation) => `- ${violation}`).join("\n")
  );
  process.exitCode = 1;
} else {
  console.log("Client/server restricted-import check passed.");
}
