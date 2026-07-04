import { db } from "./src/lib/server/db/index";
import { historicalFireData } from "./src/lib/server/db/schema";
import { sql } from "drizzle-orm";

async function main() {
  const result = await db.select({ count: sql`count(*)` }).from(historicalFireData);
  console.log("historicalFireData count:", result);
}
main().catch(console.error).then(() => process.exit(0));
