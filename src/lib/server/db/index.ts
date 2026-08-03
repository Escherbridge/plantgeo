import { drizzle } from "drizzle-orm/postgres-js";
import postgres from "postgres";
import * as schema from "./schema";

const connectionString = process.env.DATABASE_URL!;

const client = postgres(connectionString, {
  max: 20,
  idle_timeout: 30,
  // Geometry-repair NOTICEs from the 0004 sync trigger otherwise flood the
  // deployment logs on every ingestion run (thousands of lines per cron tick).
  onnotice: () => {},
});

export const db = drizzle(client, { schema });
