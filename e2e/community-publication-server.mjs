import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { createCommunityDatabase } from "./community-publication-db.mjs";

const appPort = 3307;
const tilePort = 3308;
const tileOrigin = `http://plantgeo-martin.localhost:${tilePort}`;
const fixture = await createCommunityDatabase(process.env.COMMUNITY_TEST_ADMIN_URL);
console.info("Local community browser database:", new URL(fixture.url).pathname, fixture.version);
console.info("Community fixture process:", process.pid);

const tiles = createServer(async (request, response) => {
  response.setHeader("Access-Control-Allow-Origin", "*");
  response.setHeader("Cache-Control", "no-store");
  const url = new URL(request.url ?? "/", tileOrigin);
  if (request.method === "POST" && url.pathname === "/__community_acceptance_shutdown") {
    void close(response).then(() => process.exit(0));
    return;
  }
  const coordinate = url.pathname.match(/^\/intervention_tiles\/(\d+)\/(\d+)\/(\d+)(?:\.pbf)?$/);
  if (coordinate) {
    try {
      const [z, x, y] = coordinate.slice(1).map(Number);
      const [row] = await fixture.sql`SELECT geo.intervention_tiles(${z},${x},${y}) AS tile`;
      response.setHeader("Content-Type", "application/x-protobuf");
      response.end(row.tile);
    } catch (error) {
      console.error("Local PostGIS tile failed", error);
      response.writeHead(500).end("Local PostGIS tile failed");
    }
    return;
  }
  if (url.pathname === "/intervention_tiles") {
    response.setHeader("Content-Type", "application/json");
    response.end(JSON.stringify({ tilejson: "3.0.0", tiles: [`${tileOrigin}/intervention_tiles/{z}/{x}/{y}`], minzoom: 0, maxzoom: 22 }));
    return;
  }
  if (url.pathname.startsWith("/osm_roads,osm_waterways")) {
    if (/\/\d+\/\d+\/\d+$/.test(url.pathname)) {
      response.setHeader("Content-Type", "application/x-protobuf");
      response.end(Buffer.alloc(0));
    } else {
      response.setHeader("Content-Type", "application/json");
      response.end(JSON.stringify({ tilejson: "3.0.0", tiles: [`${tileOrigin}/osm_roads,osm_waterways/{z}/{x}/{y}`], minzoom: 0, maxzoom: 22 }));
    }
    return;
  }
  response.writeHead(404).end();
});
await new Promise((resolve) => tiles.listen(tilePort, "127.0.0.1", resolve));

const childEnv = Object.fromEntries(Object.entries(process.env).filter(([key]) => /^(PATH|PATHEXT|SYSTEMROOT|WINDIR|COMSPEC|TEMP|TMP|USERPROFILE|APPDATA|LOCALAPPDATA|PROGRAMFILES|PROGRAMDATA|HOME|NUMBER_OF_PROCESSORS)$/i.test(key)));
const app = spawn(process.execPath, ["node_modules/next/dist/bin/next", "dev", "--turbopack", "-p", String(appPort), "-H", "127.0.0.1"], {
  cwd: fileURLToPath(new URL("..", import.meta.url)),
  windowsHide: true,
  stdio: "inherit",
  env: {
    ...childEnv,
    DATABASE_URL: fixture.url,
    NEXTAUTH_URL: `http://127.0.0.1:${appPort}`,
    NEXTAUTH_SECRET: "isolated-local-community-acceptance-secret-2026",
    REDIS_URL: "redis://127.0.0.1:6399",
    NEXT_PUBLIC_PMTILES_URL: "https://tiles.aevani.com/fixture.pmtiles",
    NEXT_PUBLIC_DYNAMIC_TILES_URL: tileOrigin,
    NEXT_TELEMETRY_DISABLED: "1",
  },
});
console.info("Community Next child process:", app.pid);
let closing = false;
async function close(shutdownResponse) {
  if (closing) return;
  closing = true;
  if (process.platform === "win32" && app.pid && app.exitCode === null) {
    await new Promise((resolve) => {
      const stop = spawn("taskkill.exe", ["/PID", String(app.pid), "/T", "/F"], { windowsHide: true, stdio: "inherit" });
      stop.once("exit", resolve);
      stop.once("error", resolve);
    });
  } else app.kill();
  await fixture.dispose();
  if (shutdownResponse) await new Promise((resolve) => shutdownResponse.end("Stopped this isolated acceptance fixture", resolve));
  tiles.closeAllConnections();
  await new Promise((resolve) => tiles.close(resolve));
}
for (const signal of ["SIGINT", "SIGTERM"]) process.once(signal, () => { void close().then(() => process.exit(0)); });
app.once("exit", (code) => { if (!closing) void close().then(() => process.exit(code ?? 1)); });
