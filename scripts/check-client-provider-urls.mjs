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
  ["https://plantgeo.aevani.com", "first-party API documentation example"],
  ["https://plantgeo.io", "first-party application origin"],
  ["https://plantgeo.aevani.com", "first-party public site origin for social cards"],
  ["https://example.org", "non-network form placeholder"],
  // Editorial sister-site links (footer, body, essays). Not observation sources.
  ["https://primusneo.com", "sister site: PrimusNeo"],
  ["https://neos.primusneo.com", "sister site: NEOS"],
  ["https://azoa.ardanova.com", "sister site: AZOA"],
  ["https://ardanova.com", "sister site: ArdaNova"],
  ["https://aevani.com", "sister site: Aevani"],
  ["https://overseasgenai.com", "sister site: OverseasGenAI"],
  ["https://glitxz.com", "sister site: Glitxz"],
  ["https://schema.org", "JSON-LD context for Organization and WebSite discovery metadata"],
]);

const ALLOWED_PATH_PREFIXES = new Map([
  ["http://www.w3.org/2000/svg", "SVG namespace identifier"],
  ["https://www.w3.org/2000/svg", "SVG namespace identifier"],
  ["https://tiles.aevani.com/", "first-party PMTiles archive on Cloudflare R2, see infra/tiles/AGENTS.md"],
  ["https://protomaps.github.io/basemaps-assets/", "versioned glyph and sprite assets"],
  ["https://build.protomaps.com/", "development PMTiles default; rejected by production build gate"],
  ["https://s3.amazonaws.com/elevation-tiles-prod/", "public terrain asset exception"],
  [
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/",
    "public basemap imagery exception",
  ],
  // Published citations on /site-types. The pages link the source; nothing here is read as a layer.
  ["https://research.fs.usda.gov/nrs/projects/wuigrowth", "site-type citation: USFS WUI growth"],
  [
    "https://www.fs.usda.gov/rds/archive/catalog/RDS-2015-0012-4",
    "site-type citation: 1990-2020 WUI archive",
  ],
  ["https://doi.org/10.2737/RDS-2015-0012-4", "site-type citation: WUI archive DOI"],
  [
    "https://www.fs.usda.gov/rds/archive/products/RDS-2015-0012-4/_metadata_RDS-2015-0012-4.html",
    "site-type citation: WUI archive metadata",
  ],
  [
    "https://www.govinfo.gov/content/pkg/FR-2001-01-04/pdf/01-52.pdf",
    "site-type citation: 66 Fed. Reg. 751",
  ],
  [
    "https://www.nfpa.org/-/media/Project/Storefront/Catalog/Files/Firewise/Fact-sheets/FirewiseHowToPrepareYourHomeForWildfires.pdf",
    "site-type citation: Firewise home fact sheet",
  ],
  [
    "https://www.nfpa.org/-/media/Project/Storefront/Catalog/Files/Firewise/Fact-sheets/FirewiseFactSheetsImmediateNoncombustibleZone.pdf",
    "site-type citation: Firewise immediate zone fact sheet",
  ],
  [
    "https://www.nfpa.org/education-and-research/wildfire/firewise-usa",
    "site-type citation: Firewise USA program home",
  ],
  [
    "https://www.usgs.gov/national-hydrography/national-hydrography-dataset",
    "site-type citation: NHD program page",
  ],
  ["https://www.usgs.gov/national-hydrography/science/faqs", "site-type citation: NHD FAQs"],
  [
    "https://www.usgs.gov/ngp-standards-and-specifications/national-hydrography-dataset-nhd-data-dictionary-feature-classes",
    "site-type citation: NHD data dictionary",
  ],
  ["https://www.usgs.gov/3d-hydrography-program", "site-type citation: 3D Hydrography Program"],
  [
    "https://www.epa.gov/sites/default/files/2015-09/documents/2008_04_18_nps_watershed_handbook_handbook-2.pdf",
    "site-type citation: EPA watershed handbook PDF",
  ],
  [
    "https://www.epa.gov/nps/handbook-developing-watershed-plans-restore-and-protect-our-waters",
    "site-type citation: EPA watershed handbook landing page",
  ],
  ["https://www.epa.gov/hwp", "site-type citation: EPA Healthy Watersheds"],
  ["https://www.epa.gov/watershedacademy", "site-type citation: EPA Watershed Academy"],
  ["https://websoilsurvey.nrcs.usda.gov/app/", "site-type citation: Web Soil Survey"],
  [
    "https://catalog.data.gov/dataset/soil-survey-geographic-database-ssurgo",
    "site-type citation: SSURGO data.gov record",
  ],
  [
    "https://www.fs.usda.gov/nrs/pubs/gtr/gtr_nrs122.pdf",
    "site-type citation: USFS GTR NRS-122",
  ],
  [
    "https://www.nrcs.usda.gov/resources/data-and-reports/soil-survey-geographic-database-ssurgo",
    "site-type citation: NRCS SSURGO landing page",
  ],
  // Published citations on /guides. The pages link the source; nothing here is read as a layer.
  ["https://www.fema.gov/flood-maps", "guide citation: FEMA Flood Maps"],
  [
    "https://www.fema.gov/glossary/flood-insurance-rate-map-firm",
    "guide citation: FEMA FIRM glossary",
  ],
  [
    "https://www.fema.gov/glossary/special-flood-hazard-area-sfha",
    "guide citation: FEMA SFHA glossary",
  ],
  ["https://msc.fema.gov/portal/howto", "guide citation: FEMA MSC how-to"],
  ["https://www.ready.gov/floods", "guide citation: Ready.gov floods"],
  [
    "https://www.fws.gov/program/national-wetlands-inventory",
    "guide citation: National Wetlands Inventory",
  ],
  ["https://www.usgs.gov/3d-elevation-program", "guide citation: USGS 3DEP"],
  [
    "https://www.usgs.gov/programs/national-geospatial-program/national-map",
    "guide citation: The National Map",
  ],
]);

const SOURCE_EXTENSION = /\.(?:[cm]?[jt]sx?)$/;
const URL_PATTERN = /https?:\/\/[^\s"'`)<>{}\\]+/g;
const BLOCK_COMMENT_PATTERN = /\/\*[\s\S]*?\*\//g;

// Blanks /* ... */ (incl. JSDoc) bodies so example/rejected URLs in doc comments
// aren't flagged, while keeping length/line numbers intact for match reporting.
function stripBlockComments(source) {
  return source.replace(BLOCK_COMMENT_PATTERN, (comment) => comment.replace(/[^\n]/g, " "));
}

// A template literal like `https://${host}` truncates at the regex's excluded
// `{`, leaving a trailing `$` immediately followed by `{` -- not a literal URL.
function isTemplateLiteralInterpolation(url, source, matchEnd) {
  return url.endsWith("$") && source[matchEnd] === "{";
}

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
    const source = stripBlockComments(await readFile(file.absolutePath, "utf8"));
    for (const match of source.matchAll(URL_PATTERN)) {
      const url = match[0].replace(/[.,;:]$/, "");
      if (isTemplateLiteralInterpolation(url, source, match.index + match[0].length)) continue;
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
