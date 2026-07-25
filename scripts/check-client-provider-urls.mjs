import { readdir, readFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";

const DISPLAY_ROOTS = [
  "src/app",
  "src/components",
  "src/hooks",
  "src/lib",
  "src/stores",
  "src/workers",
];

const ALLOWED_ORIGINS = new Map([
  ["https://openstreetmap.org", "map attribution link"],
  ["https://www.openstreetmap.org", "map attribution link"],
  ["https://plantgeo.app", "first-party API documentation example"],
  ["https://plantgeo.io", "first-party application origin"],
  ["https://example.org", "non-network form placeholder"],
]);

const ALLOWED_PATH_PREFIXES = new Map([
  ["http://www.w3.org/2000/svg", "SVG namespace identifier"],
  ["https://www.w3.org/2000/svg", "SVG namespace identifier"],
  ["https://protomaps.github.io/basemaps-assets/", "versioned glyph and sprite assets"],
  ["https://build.protomaps.com/", "development PMTiles default; rejected by production build gate"],
  ["https://s3.amazonaws.com/elevation-tiles-prod/", "public terrain asset exception"],
  [
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/",
    "public basemap imagery exception",
  ],
]);

const SOURCE_EXTENSION = /\.(?:[cm]?[jt]sx?)$/;
const URL_PATTERN = /https?:\/\/[^\s"'`)<>{}\\]+/g;

async function collectSourceFiles(relativeDirectory) {
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
      files.push(...(await collectSourceFiles(relativePath)));
    } else if (SOURCE_EXTENSION.test(entry.name) && !entry.name.includes(".test.")) {
      files.push({ absolutePath, relativePath });
    }
  }
  return files;
}

function isAllowed(url) {
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    return false;
  }
  if (
    parsed.protocol === "http:" &&
    (parsed.hostname === "localhost" || parsed.hostname === "127.0.0.1")
  ) {
    return true;
  }
  if (ALLOWED_ORIGINS.has(parsed.origin)) return true;
  return [...ALLOWED_PATH_PREFIXES.keys()].some((prefix) => url.startsWith(prefix));
}

const violations = [];
for (const root of DISPLAY_ROOTS) {
  for (const file of await collectSourceFiles(root)) {
    const source = await readFile(file.absolutePath, "utf8");
    for (const match of source.matchAll(URL_PATTERN)) {
      const url = match[0].replace(/[.,;:]$/, "");
      if (!isAllowed(url)) {
        const line = source.slice(0, match.index).split("\n").length;
        violations.push(`${file.relativePath}:${line} ${url}`);
      }
    }
  }
}

if (violations.length > 0) {
  console.error(
    "Unapproved browser-visible URL(s) found. Environmental facts must use a PlantGeo API, publication, or reviewed asset exception:\n" +
      violations.map((violation) => `- ${violation}`).join("\n"),
  );
  process.exitCode = 1;
} else {
  console.log(
    `Client data-boundary check passed (${ALLOWED_ORIGINS.size + ALLOWED_PATH_PREFIXES.size} documented URL rules).`,
  );
}
