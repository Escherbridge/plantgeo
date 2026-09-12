import { readFile } from "node:fs/promises";
import { randomUUID } from "node:crypto";
import postgres from "postgres";
import { hash } from "bcryptjs";
import identities from "./community-publication-identities.json" with { type: "json" };

export const COMMUNITY_PASSWORD = identities.password;
export const COMMUNITY_EMAILS = identities.emails;

/** Refuse every remote host and every existing product database. */
export function requireLocalCommunityAdmin(raw) {
  if (!raw) throw new Error("Set COMMUNITY_TEST_ADMIN_URL to a dedicated local PostgreSQL cluster.");
  const url = new URL(raw);
  if (!["postgres:", "postgresql:"].includes(url.protocol) || !["127.0.0.1", "localhost", "[::1]"].includes(url.hostname) || url.pathname !== "/postgres" || url.search) {
    throw new Error("Community acceptance requires a loopback PostgreSQL admin URL for /postgres, without query parameters.");
  }
  return url;
}

/** Extract migration-owned objects, so the spatial trigger and tile reader cannot be test imitations. */
export async function createCommunityDatabase(raw) {
  const url = requireLocalCommunityAdmin(raw);
  const admin = postgres(url.toString(), { max: 1, onnotice: () => {} });
  const name = `plantgeo_community_test_${randomUUID().replaceAll("-", "")}`;
  await admin.unsafe(`CREATE DATABASE "${name}"`);
  const databaseUrl = new URL(url);
  databaseUrl.pathname = `/${name}`;
  const sql = postgres(databaseUrl.toString(), { max: 4, onnotice: () => {} });
  let disposed = false;
  const dispose = async () => {
    if (disposed) return;
    disposed = true;
    await sql.end({ timeout: 5 });
    await admin.unsafe(`DROP DATABASE "${name}" WITH (FORCE)`);
    await admin.end({ timeout: 5 });
  };
  try {
    await sql.unsafe("CREATE EXTENSION postgis; CREATE SCHEMA geo;");
    const baseline = await readFile(new URL("../drizzle/0000_baseline.sql", import.meta.url), "utf8");
    for (const table of ["public.users", "public.teams", "public.team_members", "public.accounts", "public.sessions", "public.verification_tokens", "geo.layers", "geo.features"]) {
      const definition = baseline.match(new RegExp(`CREATE TABLE ${table.replaceAll(".", "\\.")} \\([\\s\\S]*?\\n\\);`))?.[0];
      if (!definition) throw new Error(`Baseline table missing: ${table}`);
      await sql.unsafe(definition);
    }
    for (const fn of ["sync_feature_geom_from_properties", "intervention_tiles"]) {
      const definition = baseline.match(new RegExp(`CREATE FUNCTION geo\\.${fn}\\([\\s\\S]*?\\n\\$\\$;`))?.[0];
      if (!definition) throw new Error(`Baseline function missing: ${fn}`);
      await sql.unsafe(definition);
    }
    const trigger = baseline.match(/CREATE TRIGGER geo_features_sync_geom[^;]+;/)?.[0];
    if (!trigger) throw new Error("Baseline geometry trigger missing");
    await sql.unsafe(trigger);
    await sql.unsafe("ALTER TABLE public.users ADD PRIMARY KEY (id); ALTER TABLE public.teams ADD PRIMARY KEY (id); ALTER TABLE geo.layers ADD PRIMARY KEY (id); ALTER TABLE geo.features ADD PRIMARY KEY (id); ALTER TABLE public.team_members ADD PRIMARY KEY (team_id,user_id); ALTER TABLE geo.features ADD FOREIGN KEY (layer_id) REFERENCES geo.layers(id); ALTER TABLE public.team_members ADD FOREIGN KEY (team_id) REFERENCES public.teams(id); ALTER TABLE public.team_members ADD FOREIGN KEY (user_id) REFERENCES public.users(id);");
    const passwordHash = await hash(COMMUNITY_PASSWORD, 10);
    /** @type {Record<string, string>} */
    const identities = {};
    for (const [role, email] of Object.entries(COMMUNITY_EMAILS)) {
      const [user] = await sql`INSERT INTO public.users (name,email,password_hash,platform_role,email_verified) VALUES (${role},${email},${passwordHash},${role},now()) RETURNING id`;
      identities[role] = user.id;
    }
    const [layer] = await sql`INSERT INTO geo.layers (name,is_public) VALUES ('interventions',true) RETURNING id`;
    const [version] = await sql`SELECT version() AS postgres, postgis_full_version() AS postgis`;
    return { sql, url: databaseUrl.toString(), identities, layerId: layer.id, version, dispose };
  } catch (error) {
    await dispose();
    throw error;
  }
}
