import { TRPCError } from "@trpc/server";
import { sql } from "drizzle-orm";
import type { Context } from "@/lib/server/trpc/init";
import type { InterventionGeometry } from "./intervention-geometry";

/** Validate original coordinates before the database trigger can repair their topology. */
export async function assertValidInterventionGeometry(db: Context["db"], geometry: InterventionGeometry): Promise<void> {
  const [validity] = await db.execute<{ valid: boolean }>(sql`
    SELECT ST_IsValid(geometry) AND NOT ST_IsEmpty(geometry) AS valid
    FROM (SELECT ST_GeomFromGeoJSON(${JSON.stringify(geometry)}) AS geometry) AS site
  `);
  if (!validity?.valid) throw new TRPCError({ code: "BAD_REQUEST", message: "The site boundary is invalid or empty. Correct its geometry before submitting or publishing." });
}
