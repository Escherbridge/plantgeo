import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import { drizzle } from "drizzle-orm/postgres-js";
import { migrate } from "drizzle-orm/postgres-js/migrator";
import { readMigrationFiles } from "drizzle-orm/migrator";
import { migrateDatabase } from "../migrate-database.mjs";

const SET_PUBLIC = "SET search_path TO public";
const EMPTY_PATH = "SELECT pg_catalog.set_config('search_path', '', false);";
const BASE_TABLE = 'CREATE TABLE public.baseline_identity (id integer);';
const ACCOUNT_TABLE = 'CREATE TABLE "accounts" (id integer);';

function fixture(t, files = [`${EMPTY_PATH}\n--> statement-breakpoint\n${BASE_TABLE}`, ACCOUNT_TABLE]) {
  const directory = mkdtempSync(path.join(os.tmpdir(), "plantgeo-migration-contract-"));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  mkdirSync(path.join(directory, "meta"));
  const entries = files.map((content, index) => {
    const tag = `000${index}_fixture`;
    writeFileSync(path.join(directory, `${tag}.sql`), content);
    return { idx: index, version: "7", when: 100 + index, tag, breakpoints: true };
  });
  writeFileSync(path.join(directory, "meta/_journal.json"), JSON.stringify({ version: "7", dialect: "postgresql", entries }));
  return { migrationsFolder: directory };
}

/** Controlled SQL transport beneath the real Drizzle driver; PostgreSQL execution is validated separately. */
function transport({ ledger = [], failOn = null } = {}) {
  const state = { searchPath: '"$user", public', ledger: [...ledger], tables: [], trace: [], transactions: 0, commits: 0, rollbacks: 0, inTransaction: false };
  const failure = new Error("controlled migration failure");
  const client = {
    options: { parsers: {}, serializers: {} },
    async unsafe(query, params = []) {
      const text = query.trim();
      state.trace.push({ text, params, inTransaction: state.inTransaction, searchPath: state.searchPath });
      if (failOn && text === failOn) throw failure;
      if (text === SET_PUBLIC) state.searchPath = "public";
      if (text === EMPTY_PATH) state.searchPath = "";
      if (text === BASE_TABLE) state.tables.push("public.baseline_identity");
      if (text === ACCOUNT_TABLE) {
        if (!state.searchPath) throw new Error("no schema has been selected to create in");
        state.tables.push(`${state.searchPath === "public" ? "public" : "geo"}.accounts`);
      }
      if (/^select id, hash, created_at/i.test(text)) return [...state.ledger].sort((a, b) => b.created_at - a.created_at).slice(0, 1);
      if (/^insert into .*__drizzle_migrations/i.test(text)) state.ledger.push({ hash: params[0], created_at: params[1] });
      return [];
    },
    async begin(callback) {
      state.transactions += 1;
      const before = { searchPath: state.searchPath, ledger: [...state.ledger], tables: [...state.tables] };
      state.inTransaction = true;
      try {
        const result = await callback(client);
        state.commits += 1;
        return result;
      } catch (error) {
        Object.assign(state, before);
        state.rollbacks += 1;
        throw error;
      } finally {
        state.inTransaction = false;
      }
    },
  };
  return { db: drizzle(client), state, failure };
}

test("the installed migrator reproduces empty-path leakage into the next unqualified migration", async (t) => {
  const config = fixture(t);
  const { db, state } = transport();
  await assert.rejects(migrate(db, config), /no schema has been selected/);
  assert.equal(state.transactions, 1);
  assert.equal(state.rollbacks, 1);
  assert.deepEqual(state.ledger, []);
  assert.deepEqual(state.tables, []);
});

test("canonical public boundaries preserve original hashes, SQL order and one committed transaction", async (t) => {
  const config = fixture(t);
  const original = readMigrationFiles(config);
  const { db, state } = transport();
  await migrateDatabase(db, config);
  assert.equal(state.transactions, 1);
  assert.equal(state.commits, 1);
  assert.equal(state.rollbacks, 0);
  assert.equal(state.searchPath, "public");
  assert.deepEqual(state.tables, ["public.baseline_identity", "public.accounts"]);
  assert.deepEqual(state.ledger, original.map(({ hash, folderMillis }) => ({ hash, created_at: folderMillis })));
  assert.deepEqual(readMigrationFiles(config), original);
  const tx = state.trace.filter((entry) => entry.inTransaction);
  const expected = original.flatMap((migration) => [...migration.sql.map((stmt) => stmt.trim()), SET_PUBLIC, "LEDGER"]);
  assert.deepEqual(tx.map(({ text }) => /^insert into .*__drizzle_migrations/i.test(text) ? "LEDGER" : text), expected);
  assert.equal(state.trace[0].text, SET_PUBLIC);
  assert.equal(state.trace.at(-1).text, SET_PUBLIC);
  assert.equal(state.trace.at(-1).inTransaction, false);
  for (const migration of original) {
    const tag = `000${migration.folderMillis - 100}_fixture`;
    assert.equal(migration.hash, createHash("sha256").update(readFileSync(path.join(config.migrationsFolder, `${tag}.sql`))).digest("hex"));
  }
});

test("an already-current database receives canonical public without executing historical SQL or writing the ledger", async (t) => {
  const config = fixture(t);
  const originals = readMigrationFiles(config);
  const ledger = originals.map(({ hash, folderMillis }) => ({ hash, created_at: folderMillis }));
  const { db, state } = transport({ ledger });
  await migrateDatabase(db, config);
  assert.equal(state.searchPath, "public");
  assert.deepEqual(state.ledger, ledger);
  assert.deepEqual(state.tables, []);
  assert.deepEqual(state.trace.filter((entry) => entry.inTransaction), []);
  assert.equal(state.transactions, 1);
  assert.equal(state.commits, 1);
});

test("an existing baseline ledger skips its SQL and keeps the next unqualified table in public", async (t) => {
  const config = fixture(t);
  const original = readMigrationFiles(config);
  const { db, state } = transport({ ledger: [{ hash: original[0].hash, created_at: original[0].folderMillis }] });
  await migrateDatabase(db, config);
  assert.deepEqual(state.tables, ["public.accounts"]);
  assert.ok(!state.trace.some(({ text }) => text === EMPTY_PATH));
  assert.equal(state.ledger.length, 2);
  assert.equal(state.ledger[1].hash, original[1].hash);
});

test("migration failure rolls the pending batch back and propagates the original error without cleanup SQL masking it", async (t) => {
  const fail = "SELECT controlled_failure();";
  const config = fixture(t, [`${EMPTY_PATH}\n--> statement-breakpoint\n${BASE_TABLE}`, fail, ACCOUNT_TABLE]);
  const { db, state, failure } = transport({ failOn: fail });
  await assert.rejects(migrateDatabase(db, config), (error) => error === failure);
  assert.equal(state.transactions, 1);
  assert.equal(state.commits, 0);
  assert.equal(state.rollbacks, 1);
  assert.deepEqual(state.ledger, []);
  assert.deepEqual(state.tables, []);
  assert.equal(state.searchPath, "public");
  assert.equal(state.trace.at(-1).text, fail);
  assert.ok(!state.trace.some(({ text }) => text === ACCOUNT_TABLE));
});

test("the repository migration packet is executed without changing its files or original ledger hashes", async () => {
  const migrationsFolder = fileURLToPath(new URL("../../drizzle", import.meta.url));
  const original = readMigrationFiles({ migrationsFolder });
  const { db, state } = transport();
  await migrateDatabase(db, { migrationsFolder });
  assert.deepEqual(state.ledger, original.map(({ hash, folderMillis }) => ({ hash, created_at: folderMillis })));
  assert.deepEqual(readMigrationFiles({ migrationsFolder }), original);
  const statements = state.trace.filter(({ inTransaction, text }) => inTransaction && !/^insert into .*__drizzle_migrations/i.test(text)).map(({ text }) => text);
  assert.deepEqual(statements, original.flatMap((migration) => [...migration.sql.map((stmt) => stmt.trim()), SET_PUBLIC]));
});
