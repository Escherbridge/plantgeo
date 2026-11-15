import type { PostgresJsDatabase } from "drizzle-orm/postgres-js";
import type * as schema from "@/lib/server/db/schema";

/** The application database. Type-only so affiliate helpers do not open a pool. */
export type AppDatabase = PostgresJsDatabase<typeof schema>;
