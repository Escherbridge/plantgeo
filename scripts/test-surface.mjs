import { execFileSync, spawnSync } from "node:child_process";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../", import.meta.url));
const testFile = /\.(test|spec)\.[cm]?[jt]sx?$/;
const executable = /\.[cm]?[jt]sx?$/;
const fsContract = /\b(?:readFileSync|readFile|readdirSync|execFileSync|spawnSync)\s*\(/;
const shared = /^(?:src\/(?:test\/|types\/|lib\/server\/(?:db\/|trpc\/(?:index|trpc|root)\.))|(?:package(?:-lock)?\.json|(?:vitest|vite|tsconfig|next|eslint|postcss|tailwind|playwright)\.)|scripts\/|drizzle\/|infra\/|Dockerfile|docker-compose|\.husky\/)/;

export const batches = {
  ai: /(?:regional|remediation|ai-|soil-ai|source-attribution|evidence-freshness|SavedConversation)/i,
  parquet: /(?:parquet|climate|soil|vegetation|environmental|observation-day|signal-census|pre-aggregation|water|TimeSlider)/i,
  map: /(?:\/components\/|\/stores\/|\/hooks\/|\/lib\/(?:map|geo)\/)/,
  identity: /(?:\/security\/|auth|moderation|org-|teams-directory|tracking-access)/i,
  api: /(?:\/api\/|\/trpc\/|\/services\/|\/security\/|\/lib\/server\/)/,
  offline: /(?:offline|tile-cache|sync-queue)/i,
  contracts: fsContract,
};

function git(cwd, args) {
  return execFileSync("git", args, { cwd, encoding: "utf8", maxBuffer: 8 * 1024 * 1024 });
}

export function changedFiles(cwd, base) {
  const revision = base
    ? git(cwd, ["merge-base", "HEAD", git(cwd, ["rev-parse", "--verify", "--end-of-options", `${base}^{commit}`]).trim()]).trim()
    : "HEAD";
  return [...new Set([
    ...git(cwd, ["diff", "--name-only", "--no-renames", "-z", revision, "--"]).split("\0"),
    ...git(cwd, ["ls-files", "--others", "--exclude-standard", "-z"]).split("\0"),
  ].filter(Boolean))].sort();
}

export function discoverTests(cwd) {
  const found = [];
  function visit(directory) {
    if (!existsSync(path.join(cwd, directory))) return;
    for (const entry of readdirSync(path.join(cwd, directory), { withFileTypes: true })) {
      const name = `${directory}/${entry.name}`;
      if (entry.isDirectory()) visit(name);
      else if (entry.isFile() && testFile.test(name)) found.push(name);
    }
  }
  visit("src");
  return found.sort();
}

export function planTests({ changed = [], batch = [], tests, read, exists }) {
  const contracts = tests.filter(file => fsContract.test(read(file)));
  if (batch.length) {
    const unknown = batch.filter(name => name !== "full" && !Object.hasOwn(batches, name));
    if (unknown.length) throw new Error(`Unknown test batch: ${unknown.join(", ")}`);
    if (batch.includes("full")) return { mode: "full", reason: "Explicit full batch", files: [], contracts: [] };
    const files = tests.filter(file => batch.some(name => name === "contracts"
      ? contracts.includes(file) : batches[name].test(file)));
    if (!files.length) throw new Error("The requested batch has no tests");
    return { mode: "batch", reason: `Explicit batches: ${batch.join(", ")}`, files: [...new Set([...files, ...contracts])].sort(), contracts };
  }
  const files = [];
  for (const file of changed) {
    if (/\.(?:md|mdx|txt)$/.test(file) || /^(?:conductor|docs)\//.test(file)) continue;
    // Python owns its checks; SQL and manifests remain cross-stack contracts.
    if (/^services\/agri-data-service\//.test(file)) {
      if (/\.(?:py|toml|lock)$/.test(file)) continue;
      return { mode: "full", reason: `Cross-stack data contract: ${file}`, files: [], contracts: [] };
    }
    if (shared.test(file) || !file.startsWith("src/") || !executable.test(file) || !exists(file)) {
      return { mode: "full", reason: `Shared, removed, or unclassified path: ${file}`, files: [], contracts: [] };
    }
    files.push(file);
  }
  if (!files.length) return { mode: "none", reason: "No JavaScript/TypeScript test surface changed; other language checks may still be required", files: [], contracts: [] };
  return { mode: "related", reason: "Source-only dependency tests must pass before the filesystem/command contract batch", files: [...new Set(files)].sort(), contracts };
}

export function parseArgs(args) {
  const options = { changed: false, batch: [], plan: false, list: false, base: undefined };
  for (let i = 0; i < args.length; i++) {
    const argument = args[i];
    if (argument === "--changed") options.changed = true;
    else if (argument === "--plan") options.plan = true;
    else if (argument === "--list-batches") options.list = true;
    else if (argument === "--base" || argument === "--batch") {
      const value = args[++i];
      if (!value || value.startsWith("--")) throw new Error(`${argument} requires a value`);
      if (argument === "--base") { options.base = value; options.changed = true; }
      else options.batch.push(value);
    } else throw new Error(`Unknown option: ${argument}`);
  }
  if (options.changed && options.batch.length) throw new Error("Use --changed/--base or --batch, not both");
  if (options.list && (options.changed || options.batch.length || options.plan)) throw new Error("--list-batches cannot be combined with run selection or --plan");
  if (!options.changed && !options.batch.length && !options.list) throw new Error("Choose --changed, --base REF, --batch NAME, or --list-batches");
  return options;
}

export function commandsFor(plan, toolingTests) {
  if (plan.mode === "none") return [];
  const common = ["--configLoader", "runner", "--maxWorkers=2"];
  const vitest = "node_modules/vitest/vitest.mjs";
  if (plan.mode === "full") return [
    ["--test", ...toolingTests],
    [vitest, "run", ...common],
  ];
  const commands = [[vitest, plan.mode === "related" ? "related" : "run", ...plan.files, ...common, "--run"]];
  if (plan.mode === "related" && plan.contracts.length) commands.push([vitest, "run", ...plan.contracts, ...common]);
  return commands;
}

function main() {
  const options = parseArgs(process.argv.slice(2));
  if (options.list) {
    console.log(JSON.stringify({ batches: [...Object.keys(batches), "full"] }, null, 2));
    return;
  }
  const changed = options.changed ? changedFiles(root, options.base) : [];
  const plan = planTests({
    changed, batch: options.batch, tests: discoverTests(root),
    read: file => readFileSync(path.join(root, file), "utf8"),
    exists: file => existsSync(path.join(root, file)),
  });
  const toolingTests = readdirSync(path.join(root, "scripts/tests")).filter(file => file.endsWith(".test.mjs")).map(file => `scripts/tests/${file}`);
  const commands = commandsFor(plan, toolingTests);
  console.log(JSON.stringify({ ...plan, changed, base: options.base ?? null, commands, full_suite: plan.mode === "full" }, null, 2));
  if (options.plan) return;
  for (const command of commands) {
    const result = spawnSync(process.execPath, command, { cwd: root, stdio: "inherit" });
    if (result.error) throw result.error;
    if (result.status !== 0) { process.exitCode = result.status ?? 1; return; }
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try { main(); } catch (error) { console.error(error.message); process.exitCode = 1; }
}
