import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, mkdirSync, renameSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { changedFiles, commandsFor, parseArgs, planTests } from "../test-surface.mjs";

const tests = ["src/ai-prompt.test.ts", "src/map.test.ts", "src/contract.test.ts"];
const plan = (changed, extra = {}) => planTests({ changed, tests, read: file => file.includes("contract") ? "readFileSync('schema.sql')" : "", exists: () => true, ...extra });

test("changed source uses the dependency graph and includes filesystem contracts", () => {
  const selection = plan(["src/lib/server/services/ai-prompt.ts"]);
  assert.equal(selection.mode, "related");
  assert.deepEqual(selection.files, ["src/lib/server/services/ai-prompt.ts"]);
  assert.deepEqual(selection.contracts, ["src/contract.test.ts"]);
  assert.equal(commandsFor(selection, [])[0][1], "related");
  assert.ok(!commandsFor(selection, [])[0].includes("--passWithNoTests"));
  assert.ok(!commandsFor(selection, [])[0].includes("src/contract.test.ts"));
  assert.ok(commandsFor(selection, [])[1].includes("src/contract.test.ts"));
});

test("shared configuration, deleted source, and unknown runtime paths require the full suite", () => {
  for (const file of ["package-lock.json", "vitest.config.ts", "src/test/setup.ts", "src/lib/server/db/schema.ts", "src/lib/fixture.json", "src/app/globals.css", "public/sw.js", "scripts/migrate.mjs", "new-runtime.toml"]) {
    assert.equal(plan([file]).mode, "full", file);
  }
  assert.equal(plan(["src/lib/deleted.ts"], { exists: () => false }).mode, "full");
  assert.equal(commandsFor(plan(["vitest.config.ts"]), ["scripts/tests/test-surface.test.mjs"]).length, 2);
});

test("documentation and Python source do not invoke the frontend suite; shared payloads do", () => {
  assert.equal(plan(["conductor/tracks/a/metadata.json", "docs/testing.md"]).mode, "none");
  assert.equal(plan(["services/agri-data-service/src/module.py"]).mode, "none");
  assert.equal(plan(["services/agri-data-service/tests/contract/fixtures/day.json"]).mode, "full");
  assert.deepEqual(commandsFor(plan([]), []), []);
});

test("explicit batches include contracts and reject unknown or empty selections", () => {
  const selection = plan([], { batch: ["ai"] });
  assert.equal(selection.mode, "batch");
  assert.deepEqual(selection.files, ["src/ai-prompt.test.ts", "src/contract.test.ts"]);
  assert.throws(() => plan([], { batch: ["typo"] }), /Unknown/);
  assert.throws(() => plan([], { batch: ["offline"] }), /no tests/);
});

test("CLI rejects conflicting and incomplete requests", () => {
  assert.throws(() => parseArgs(["--changed", "--batch", "ai"]), /not both/);
  assert.throws(() => parseArgs(["--base"]), /requires a value/);
  assert.throws(() => parseArgs(["--batch", "--plan"]), /requires a value/);
  assert.throws(() => parseArgs(["--unknown"]), /Unknown option/);
  assert.throws(() => parseArgs(["--list-batches", "--changed"]), /cannot be combined/);
  assert.throws(() => parseArgs(["--list-batches", "--plan"]), /cannot be combined/);
  assert.throws(() => parseArgs([]), /Choose/);
  assert.deepEqual(parseArgs(["--base", "main", "--plan"]).base, "main");
});

test("git change discovery includes staged, unstaged, untracked and both rename paths", () => {
  const directory = mkdtempSync(path.join(os.tmpdir(), "plantgeo-test-surface-"));
  const run = args => execFileSync("git", args, { cwd: directory, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] });
  try {
    run(["init", "-q"]);
    run(["config", "user.email", "tests@example.invalid"]);
    run(["config", "user.name", "Test"]);
    mkdirSync(path.join(directory, "src"));
    writeFileSync(path.join(directory, "src/old name.ts"), "export const old = 1;\n");
    writeFileSync(path.join(directory, "src/edited.ts"), "export const value = 1;\n");
    run(["add", "."]);
    run(["-c", "core.hooksPath=/dev/null", "commit", "-qm", "initial"]);
    const initial = run(["rev-parse", "HEAD"]).trim();
    renameSync(path.join(directory, "src/old name.ts"), path.join(directory, "src/new name.ts"));
    run(["add", "."]);
    writeFileSync(path.join(directory, "src/edited.ts"), "export const value = 2;\n");
    writeFileSync(path.join(directory, "src/untracked.ts"), "export const fresh = 1;\n");
    const expected = ["src/edited.ts", "src/new name.ts", "src/old name.ts", "src/untracked.ts"];
    assert.deepEqual(changedFiles(directory), expected);
    assert.deepEqual(changedFiles(directory, initial), expected);
    run(["-c", "core.hooksPath=/dev/null", "commit", "-qm", "rename"]);
    assert.deepEqual(changedFiles(directory, initial), expected);
    assert.throws(() => changedFiles(directory, "missing-ref"));
  } finally {
    assert.ok(path.resolve(directory).startsWith(`${path.resolve(os.tmpdir())}${path.sep}plantgeo-test-surface-`));
    rmSync(directory, { recursive: true, force: true });
  }
});
