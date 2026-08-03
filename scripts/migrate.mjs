// Applies pending Drizzle migrations from the runtime image; Railway runs this
// as the plantgeo-main preDeployCommand before the release receives traffic.
import { fileURLToPath } from "node:url";
import { drizzle } from "drizzle-orm/postgres-js";
import { migrate } from "drizzle-orm/postgres-js/migrator";
import postgres from "postgres";

// The migrator always issues CREATE SCHEMA/TABLE IF NOT EXISTS for its ledger,
// which needs CREATE on the database even when nothing is pending. Set
// MIGRATION_DATABASE_URL when the runtime DSN is reduced to least privilege.
const connectionString =
  process.env.MIGRATION_DATABASE_URL || process.env.DATABASE_URL;
if (!connectionString) {
  console.error(
    "migrate: neither MIGRATION_DATABASE_URL nor DATABASE_URL is set; refusing to deploy"
  );
  process.exit(1);
}

// Resolved from this file so the folder is found regardless of the working
// directory Railway starts the pre-deploy container in.
const migrationsFolder = fileURLToPath(new URL("../drizzle", import.meta.url));

// Mirrors src/lib/server/db/index.ts: no forced TLS, because production reaches
// PostgreSQL over Railway's private network. Notices from the 0004 geometry
// trigger are silenced, and DDL stays on a single session.
const client = postgres(connectionString, {
  max: 1,
  idle_timeout: 5,
  connect_timeout: 30,
  onnotice: () => {},
});

try {
  await migrate(drizzle(client), { migrationsFolder });
  console.log("migrate: drizzle migrations are up to date");
} catch (error) {
  console.error("migrate: failed to apply drizzle migrations");
  console.error(error);
  process.exitCode = 1;
} finally {
  // A pool that will not close cleanly must not turn a successful migration
  // into a failed deploy.
  await client.end({ timeout: 5 }).catch(() => {});
}
