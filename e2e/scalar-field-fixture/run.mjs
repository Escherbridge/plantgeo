import { build } from "esbuild";
import { chromium } from "@playwright/test";
import { createServer } from "node:http";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import { createHash } from "node:crypto";
import path from "node:path";
import { fileURLToPath } from "node:url";

const fixture = path.dirname(fileURLToPath(import.meta.url));
const scenarioFilter = process.argv.find((argument) => argument.startsWith("--scenario="))?.slice("--scenario=".length);
const deviceFilter = process.argv.find((argument) => argument.startsWith("--device="))?.slice("--device=".length);
const glDiagnostics = process.argv.includes("--gl-diagnostics");
const repository = path.resolve(fixture, "../..");
const runId = `run-${new Date().toISOString().replace(/[:.]/g, "-")}-${process.pid}`;
const output = path.join(repository, ".tmp/scalar-field-fixture", runId);
const runtimeFiles = [
  "e2e/scalar-field-fixture/entry.tsx", "e2e/scalar-field-fixture/run.mjs",
  "src/components/map/layers/VegetationLayer.tsx", "src/components/map/HoverTooltip.tsx",
  "src/lib/map/scalar-field.ts", "src/lib/map/scalar-field-layer.ts",
  "src/lib/map/scalar-field-inspection.ts",
  "src/lib/map/hover-fields.ts", "src/lib/map/layer-utils.ts",
  "src/lib/vegetation.ts", "src/stores/vegetation-store.ts", "package-lock.json",
];
async function sourceHashes() {
  return Object.fromEntries(await Promise.all(runtimeFiles.map(async (file) => [
    file, createHash("sha256").update((await readFile(path.join(repository, file), "utf8")).replace(/\r\n/g, "\n"), "utf8").digest("hex"),
  ])));
}
const sourceHashBefore = await sourceHashes();
await mkdir(output, { recursive: true });
console.log(JSON.stringify({ runDir: output, scenarios: scenarioFilter ?? "all", devices: deviceFilter ?? "all" }));
await build({
  entryPoints: [path.join(fixture, "entry.tsx")], outfile: path.join(output, "bundle.js"),
  bundle: true, platform: "browser", jsx: "automatic", alias: { "@": path.join(repository, "src") },
  define: {
    "process.env.NODE_ENV": '"production"',
    "process.env.NEXT_PUBLIC_SCALAR_FIELD_RENDERER_LAYERS": '"vegetation"',
  },
});

const html = `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>
*,*::before,*::after{box-sizing:border-box}
html,body,#map{margin:0;width:100%;height:100%;overflow:hidden}#map{position:absolute}.maplibregl-canvas{position:absolute}
#title{position:absolute;z-index:3;top:10px;left:10px;background:#15222b;color:#fff;font:12px Arial;padding:8px;max-width:90%}
#root{position:absolute;inset:0;pointer-events:none;font:12px Arial;color:#fff}
#root>div{position:absolute;max-width:240px;background:#15222bf2;padding:12px;border:1px solid #667781;border-radius:8px}
#root p{margin:0 0 5px}#root button{pointer-events:auto;position:absolute;right:0;top:0;width:44px;height:44px;background:#15222b;color:#fff;border:0}
#root svg{width:14px;height:14px}
</style></head><body><div id="map"></div><div id="root"></div><div id="title">SYNTHETIC vegetation fixture · no live data</div><script src="/bundle.js"></script></body></html>`;
const server = createServer(async (request, response) => {
  if (request.url === "/bundle.js") {
    response.setHeader("Content-Type", "text/javascript");
    response.end(await readFile(path.join(output, "bundle.js")));
  } else {
    response.setHeader("Content-Type", "text/html");
    response.end(html);
  }
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const address = server.address();
if (!address || typeof address === "string") throw new Error("Missing local fixture address");
const url = `http://127.0.0.1:${address.port}/`;
const results = [];
const failures = [];
let browser;

function check(condition, name, detail) {
  if (!condition) failures.push({ name, detail });
}

function near(actual, expected, tolerance = 3) {
  return actual?.slice(0, 3).every((channel, index) => Math.abs(channel - expected[index]) <= tolerance);
}

// Decode captured PNGs rather than relying on driver-dependent readPixels timing.
async function analyze(page, png, probes) {
  return page.evaluate(async ({ encoded, points }) => {
    const image = new Image();
    image.src = `data:image/png;base64,${encoded}`;
    await image.decode();
    const canvas = document.createElement("canvas");
    canvas.width = image.width;
    canvas.height = image.height;
    const context = canvas.getContext("2d");
    if (!context) throw new Error("No PNG analysis canvas");
    context.drawImage(image, 0, 0);
    const bytes = context.getImageData(0, 0, canvas.width, canvas.height).data;
    const samples = {};
    for (const [name, point] of Object.entries(points)) {
      const x = Math.round(point.x);
      const y = Math.round(point.y);
      if (x < 0 || y < 0 || x >= canvas.width || y >= canvas.height) continue;
      samples[name] = [...bytes.slice((y * canvas.width + x) * 4, (y * canvas.width + x) * 4 + 4)];
    }
    let nonBackgroundPixels = 0;
    for (let offset = 0; offset < bytes.length; offset += 4) {
      if (bytes[offset] !== 21 || bytes[offset + 1] !== 34 || bytes[offset + 2] !== 43) nonBackgroundPixels++;
    }
    return { samples, nonBackgroundPixels };
  }, { encoded: png.toString("base64"), points: probes });
}

async function inspectRefusalCell(page, device, point, value) {
  if (device.hasTouch) await page.touchscreen.tap(point.x, point.y);
  else {
    await page.mouse.move(5, 5);
    await page.mouse.move(point.x, point.y);
  }
  if (value !== null) {
    await page.getByText(`NDVI: ${value} (dimensionless)`, { exact: true }).waitFor();
    const bounds = await page.locator("#root > div").boundingBox();
    check(bounds !== null && bounds.x >= 0 && bounds.y >= 0
      && bounds.x + bounds.width <= device.viewport.width && bounds.y + bounds.height <= device.viewport.height,
    `${device.name}-refusal-layout-tooltip`, { bounds, viewport: device.viewport });
    return bounds;
  }
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  return null;
}

async function refusalLifecycle(page, device, initial, prefix) {
  const cycles = [];
  let locked = initial;
  for (const replacement of ["empty", "null"]) {
    if (replacement === "null") locked = await page.evaluate(() => window.runScalarCase("refusal-layout"));
    const key = `${prefix}-${replacement}`;
    check(locked.diagnostics.coarseCustomActive === true && locked.diagnostics.coarseCustomReady === true,
      key, "The valid coarse custom field must precede label layout");
    check(locked.diagnostics.layoutPending === true && locked.diagnostics.dataPending === false
      && locked.diagnostics.idleHeld === true && locked.diagnostics.heldIdleEvents > 0,
    key, { required: "Actual label layout must be pending with SDK idle delivery held", diagnostics: locked.diagnostics });
    check(locked.nativeOpacity === 1 && locked.labelCount > 0 && locked.inspectablePickCount > 0,
      key, "Old native cells, labels, and inspection must be present before refusal");
    const priorTooltipBounds = await inspectRefusalCell(page, device, locked.probes.negative, -0.5);
    const priorTooltipVisible = await page.locator("#root > div").isVisible();
    check(priorTooltipVisible, key, "An actual old mouse/touch tooltip must exist before replacement");
    const beforeScreenshot = `${key}-before.png`;
    await page.screenshot({ path: path.join(output, beforeScreenshot) });

    const suppressed = await page.evaluate((value) => window.runScalarRefusalPhase(value), replacement);
    await page.locator("#root > div").waitFor({ state: "hidden" });
    const priorTooltipCleared = await page.locator("#root > div").count() === 0;
    await inspectRefusalCell(page, device, suppressed.probes.negative, null);
    const reinspectionBlocked = await page.locator("#root > div").count() === 0;
    const png = await page.locator("#map canvas").screenshot({ style: "#title,#root{visibility:hidden!important}" });
    const pixels = await analyze(page, png, suppressed.probes);
    check(suppressed.diagnostics.layoutPending === true && suppressed.diagnostics.dataPending === true
      && suppressed.diagnostics.idleHeld === true && suppressed.diagnostics.sourceFeatureCount === 9
      && suppressed.diagnostics.renderedSourceFeatureCount > 0 && suppressed.unrestrictedPickCount > 0,
    key, { required: "Refused source replacement must remain serialized behind the layout lock", diagnostics: suppressed.diagnostics });
    check(suppressed.diagnostics.active === false && !suppressed.diagnostics.meshCells
      && suppressed.diagnostics.nativeSuppressed === true && suppressed.nativeOpacity === 0
      && suppressed.diagnostics.outlineOpacity === 0 && suppressed.diagnostics.labelOpacity === 0,
    key, { required: "Custom cells and native fill, outline, and label paint must be suppressed before idle", diagnostics: suppressed.diagnostics });
    check(pixels.nonBackgroundPixels === 0, key, { required: "No old cells, outlines, or labels may paint before idle", pixels });
    check(suppressed.inspectablePickCount === 0 && priorTooltipCleared && reinspectionBlocked,
      key, { priorTooltipCleared, reinspectionBlocked, inspectablePickCount: suppressed.inspectablePickCount });
    const suppressedScreenshot = `${key}-suppressed.png`;
    await page.screenshot({ path: path.join(output, suppressedScreenshot) });

    const settled = await page.evaluate(() => window.runScalarRefusalPhase("settle"));
    check(settled.diagnostics.idleHeld === false && settled.diagnostics.sourceFeatureCount === 0
      && settled.diagnostics.renderedSourceFeatureCount === 0 && settled.diagnostics.dataPending === false
      && settled.diagnostics.layoutPending === false && settled.labelCount === 0 && settled.inspectablePickCount === 0,
    key, { required: "Released idle and source completion must converge to the empty source", diagnostics: settled.diagnostics });
    const recovered = await page.evaluate(() => window.runScalarRefusalPhase("recover"));
    check(recovered.diagnostics.nativeSuppressed === false && recovered.nativeOpacity === 1
      && recovered.labelCount > 0 && recovered.inspectablePickCount > 0,
    key, "A later valid source must restore native detail and inspection");
    const recoveredTooltipBounds = await inspectRefusalCell(page, device, recovered.probes.negative, -0.4);
    const recoveredScreenshot = `${key}-recovered.png`;
    await page.screenshot({ path: path.join(output, recoveredScreenshot) });
    for (const [phase, row] of Object.entries({ locked, suppressed, settled, recovered })) {
      check(row.errors.length === 0, `${key}-${phase}`, row.errors);
    }
    cycles.push({ replacement, locked, suppressed: { ...suppressed, ...pixels }, settled, recovered,
      interaction: device.hasTouch ? "native touch refusal lifecycle" : "native mouse refusal lifecycle",
      priorTooltipVisible, priorTooltipCleared, reinspectionBlocked, priorTooltipBounds, recoveredTooltipBounds,
      screenshots: { before: beforeScreenshot, suppressed: suppressedScreenshot, recovered: recoveredScreenshot } });
  }
  return { ...cycles.at(-1).recovered, refusalCycles: cycles };
}

try {
  browser = await chromium.launch({
    headless: true, args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"],
  });
  for (const device of [
    { name: "desktop", viewport: { width: 1280, height: 720 }, isMobile: false, hasTouch: false },
    { name: "mobile", viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true },
  ]) {
    const { name, ...options } = device;
    if (deviceFilter && deviceFilter !== name) continue;
    const page = await browser.newPage({ ...options, deviceScaleFactor: 1 });
    const pageErrors = [];
    const blockedRequests = [];
    page.on("pageerror", (error) => pageErrors.push(String(error)));
    await page.route("**/*", (route) => {
      if (route.request().url().startsWith(url)) return route.continue();
      blockedRequests.push(route.request().url());
      return route.abort();
    });
    await page.goto(glDiagnostics ? `${url}?gl-diagnostics=1` : url);
    await page.waitForFunction(() => window.fixtureReady);
    let baseline;
    const cases = [
      ...["field", "duplicate", "half-opacity", "missing", "mixed-days", "mixed-units", "empty", "detail", "mode-race", "refusal-layout", "reload", "globe", "pitch"].map((scenario) => ({ scenario, sequence: "standard" })),
      ...(!scenarioFilter && name === "desktop" ? ["field", "detail", "reload"].map((scenario) => ({ scenario, sequence: "direct" })) : []),
    ];
    for (const { scenario, sequence } of cases) {
      if (scenarioFilter && !scenarioFilter.split(",").includes(scenario)) continue;
      let row = await page.evaluate((value) => window.runScalarCase(value), scenario);
      await page.locator("#title").evaluate((element, text) => { element.textContent = text; }, `SYNTHETIC NDVI | ${name} | ${scenario} | no live data`);
      const prefix = `${name}-${sequence === "direct" ? "direct-" : ""}${scenario}`;
      if (scenario === "refusal-layout") row = await refusalLifecycle(page, device, row, prefix);
      const png = await page.locator("#map canvas").screenshot({ style: "#title,#root{visibility:hidden!important}" });
      const pixels = await analyze(page, png, row.probes);
      const digest = createHash("sha256").update(png).digest("hex");
      if (scenario === "field") baseline = { digest, pixels };
      check(row.webgl2, prefix, "WebGL2 must be available for this fixture");
      check(row.errors.length === 0 && pageErrors.length === 0, prefix, { mapErrors: row.errors, pageErrors });
      if (["field", "duplicate", "missing", "reload"].includes(scenario)) {
        check(row.customPresent && row.nativeOpacity === 0, prefix, "Custom field must own low-spacing pixels");
        check(near(pixels.samples.negative, [140, 81, 10]), prefix, { negative: pixels.samples.negative });
        check(near(pixels.samples.positive, [1, 88, 79]), prefix, { positive: pixels.samples.positive });
        check(near(pixels.samples.outside, [21, 34, 43]), prefix, "Outside support must stay blank");
      }
      if (scenario === "field") {
        check(near(pixels.samples.center, [246, 232, 195]), prefix, { center: pixels.samples.center });
        check(near(pixels.samples.leftEdge, [140, 81, 10]) && near(pixels.samples.rightEdge, [246, 232, 195]), prefix, "Value boundary must remain a hard support edge");
      }
      if (scenario === "duplicate" && !scenarioFilter) check(digest === baseline?.digest, prefix, "Duplicate cells must not change the canvas PNG");
      if (scenario === "half-opacity") {
        check(row.customPresent && row.nativeOpacity === 0, prefix, "Custom field must own fractional-opacity pixels");
        check(near(pixels.samples.negative, [81, 58, 27]), prefix, { negative: pixels.samples.negative, expectedAlpha: 0.5 });
        check(near(pixels.samples.center, [134, 133, 119]), prefix, { center: pixels.samples.center, expectedAlpha: 0.5 });
      }
      if (scenario === "missing") check(near(pixels.samples.center, [21, 34, 43]), prefix, "Missing central support must stay blank");
      if (["mixed-days", "mixed-units", "globe", "pitch"].includes(scenario)) check(row.nativeOpacity === 1, prefix, "Incompatible day/unit/projection/pitch must preserve native fill");
      if (scenario === "empty") check(pixels.nonBackgroundPixels === 0, prefix, pixels);
      if (scenario === "detail" || scenario === "mode-race") {
        if (scenario === "mode-race") check(row.diagnostics.modeRaceStartedWhileSourceLoading === true, prefix, "Source switch must occur while replacement data is loading");
        check(row.nativeOpacity === 1 && row.labelCount > 0, prefix, "Native inspection fill and numeric labels must be present");
        check(row.unrestrictedPickCount > 0, prefix, "Unrestricted map click query must return the displayed cell safely");
        const point = row.probes.negative;
        if (device.hasTouch) await page.touchscreen.tap(point.x, point.y);
        else await page.mouse.move(point.x, point.y);
        const expectedValue = scenario === "mode-race" ? -0.4 : -0.5;
        await page.getByText(`NDVI: ${expectedValue} (dimensionless)`, { exact: true }).waitFor();
        check(await page.getByText("Grid: synthetic-quarter-degree", { exact: true }).isVisible(), prefix, "Exact grid metadata must be inspectable");
        const tooltipBounds = await page.locator("#root > div").boundingBox();
        check(tooltipBounds !== null
          && tooltipBounds.x >= 0 && tooltipBounds.y >= 0
          && tooltipBounds.x + tooltipBounds.width <= device.viewport.width
          && tooltipBounds.y + tooltipBounds.height <= device.viewport.height,
        prefix, { tooltipBounds, viewport: device.viewport });
        row.tooltipBounds = tooltipBounds;
        row.interaction = device.hasTouch ? "native touch tooltip passed" : "native mouse tooltip passed";
      }
      await page.screenshot({ path: path.join(output, `${prefix}.png`) });
      results.push({ device: name, sequence, ...row, ...pixels, digest, screenshot: `${prefix}.png`, pageErrors: [...pageErrors] });
      console.log(JSON.stringify({ case: prefix, nativeOpacity: row.nativeOpacity, labels: row.labelCount, nonBackgroundPixels: pixels.nonBackgroundPixels }));
    }
    check(blockedRequests.length === 0, name, { unexpectedExternalRequests: blockedRequests });
    await page.close();
  }
} catch (error) {
  failures.push({ name: "fixture-runner", detail: String(error) });
} finally {
  await browser?.close();
  await new Promise((resolve) => server.close(resolve));
  check(results.length > 0, "fixture-runner", "At least one selected scenario must run");
  const sourceHashAfter = await sourceHashes();
  const changedSourceFiles = runtimeFiles.filter((file) => sourceHashBefore[file] !== sourceHashAfter[file]);
  check(changedSourceFiles.length === 0, "source-stability", { changedSourceFiles });
  await writeFile(path.join(output, "report.json"), JSON.stringify({
    fixture: true, runDir: output, sourceHashEncoding: "utf8-lf", sourceHashBefore, sourceHashAfter, results, failures,
  }, null, 2) + "\n");
}
console.log(JSON.stringify({ cases: results.length, failures, output }, null, 2));
if (failures.length) process.exitCode = 1;
