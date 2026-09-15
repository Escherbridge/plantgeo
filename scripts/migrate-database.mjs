import { sql } from "drizzle-orm";
import { readMigrationFiles } from "drizzle-orm/migrator";

const PUBLIC_SEARCH_PATH = "SET search_path TO public";

/** Preserve migration hashes and transaction semantics while isolating search paths; see scripts/AGENTS.md. */
export async function migrateDatabase(db, config) {
  const migrations = readMigrationFiles(config).map((migration) => ({
    ...migration,
    sql: [...migration.sql, PUBLIC_SEARCH_PATH],
  }));
  await db.execute(sql.raw(PUBLIC_SEARCH_PATH));
  await db.dialect.migrate(migrations, db.session, config);
  await db.execute(sql.raw(PUBLIC_SEARCH_PATH));
}
