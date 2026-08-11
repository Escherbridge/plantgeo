// @vitest-environment node

import { randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
import postgres from "postgres";
import { PgDialect } from "drizzle-orm/pg-core";
import type { SQL } from "drizzle-orm";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import {
  CLIMATE_FIELD_SIGNALS,
  CLIMATE_FIELD_SIGNAL_IDS,
  climateFieldSignalName,
} from "@/lib/environmental/climate-field";
import {
  climateFieldCellsStatement,
  climateFieldNewestDayStatement,
  soilFieldCellsStatement,
  soilFieldNewestDayStatement,
} from "@/lib/server/services/environmental-read-model";

/**
 * The reader statements, executed against a REAL PostgreSQL through the real driver stack.
 *
 * This file exists because of a defect no mock could catch and the whole rest of the suite
 * missed: `${AGE}` bound as a bare JS number arrives with NO type OID, so PostgreSQL resolved
 * `${day}::date - $n` as `date - date -> integer` and the `::timestamptz` that followed was
 * illegal — SQLSTATE 42846, raised at PARSE time, on every single call. The climate field was
 * dead on arrival and `readSoilFieldCells` had carried the same defect since it was written.
 * A unit test over a stubbed `db.execute` sees a template string and asserts nothing about it;
 * only a real parse does.
 *
 * So: byte-identical SQL, byte-identical binds. The statements come from the read model's own
 * exported builders and go through `PgDialect.sqlToQuery` into `postgres.unsafe(sql, params)`,
 * which is exactly what `db.execute` does.
 *
 * ## Running it
 *
 * Set `PLANTGEO_TEST_DATABASE_URL` to a DISPOSABLE local PostgreSQL superuser DSN, e.g.
 * `postgres://postgres:<pw>@127.0.0.1:5433/postgres`. The suite creates its own randomly
 * named database, provisions a stand-in schema in it, and drops it again — it never writes to
 * the database named in the DSN and must never be pointed at production. Unset, the whole
 * describe skips with a loud message rather than failing the sweep.
 *
 * ## What is real and what is a stand-in
 *
 * Real: every column type the statements touch, the governed views (applied from the actual
 * `drizzle/0019_soil_field_vpd.sql` and `drizzle/0020_climate_field.sql` files, not retyped
 * here), and PostgreSQL's own parse-time resolution of every bind — which is the thing under
 * test.
 *
 * Stand-in: PostGIS. `pg_available_extensions` on the reference instance does not list it, so
 * `geometry`, `ST_MakeEnvelope`, `ST_AsGeoJSON` and `&&` are shimmed below at their real
 * ARITIES and RETURN TYPES. That keeps the statements parsing as written and keeps the bbox
 * binds resolving through a function signature the way they do in production; what it does not
 * cover is spatial semantics, which the `drizzle/tests/*.test.sql` scripts own.
 */

const ADMIN_DSN = process.env.PLANTGEO_TEST_DATABASE_URL;

if (ADMIN_DSN === undefined) {
  console.warn(
    "\n[climate-field-sql-contract] SKIPPED — no real-database coverage for the reader SQL.\n" +
      "  These cases are the only thing in the suite that parses the reader statements, and a\n" +
      "  bind whose type PostgreSQL cannot resolve (SQLSTATE 42846) is invisible without them.\n" +
      "  Set PLANTGEO_TEST_DATABASE_URL to a disposable local superuser DSN to run them, e.g.\n" +
      "    PLANTGEO_TEST_DATABASE_URL=postgres://postgres:<pw>@127.0.0.1:5433/postgres\n"
  );
}

/** Where the reader looks. Anything outside the bbox or the grid must not come back. */
const BOUNDS: [number, number, number, number] = [-125, 42, -116, 49];
const OUTSIDE_BOUNDS: [number, number, number, number] = [10, 10, 11, 11];

/** The lane is stamped at exactly midnight UTC, which is what makes the bounds load-bearing. */
const REQUESTED_DAY = "2026-04-29";
const NEXT_DAY = "2026-04-30";

const dialect = new PgDialect();

/**
 * Runs a read-model statement the way `db.execute` does.
 *
 * `sqlToQuery` is drizzle's own compiler and `unsafe(sql, params)` is what its postgres-js
 * driver calls, so nothing here paraphrases the production path.
 */
async function runStatement(
  client: postgres.Sql,
  statement: SQL
): Promise<Record<string, unknown>[]> {
  const query = dialect.sqlToQuery(statement);
  return (await client.unsafe(query.sql, query.params as never[])) as unknown as Record<
    string,
    unknown
  >[];
}

/** The PostGIS surfaces the statements name, at their real arities and return types. */
const POSTGIS_SHIM = `
  CREATE DOMAIN geometry AS text;

  CREATE FUNCTION st_makeenvelope(
    double precision, double precision, double precision, double precision, integer
  ) RETURNS geometry LANGUAGE sql IMMUTABLE AS
    $$ SELECT (format('%s,%s,%s,%s', $1, $2, $3, $4))::geometry $$;

  CREATE FUNCTION st_asgeojson(geometry) RETURNS text LANGUAGE sql IMMUTABLE AS
    $$ SELECT $1::text $$;

  -- The centroid accessors the climate reader gained on 2026-08-10, when the lane started
  -- serving three shapes off one read: the cell polygon for the filled form, this point for
  -- the symbol form, and the same point as a lattice node for the contours. Every cell this
  -- suite seeds is a "lon,lat" point in disguise, so splitting the text is the honest stand-in.
  -- In production agri.spatial_cell.centroid is public.geometry(Point,4326) and these are core
  -- PostGIS, as geo.soil_field in drizzle/0014 has been calling them all along.
  CREATE FUNCTION st_x(geometry) RETURNS double precision LANGUAGE sql IMMUTABLE AS
    $$ SELECT split_part($1::text, ',', 1)::float8 $$;

  CREATE FUNCTION st_y(geometry) RETURNS double precision LANGUAGE sql IMMUTABLE AS
    $$ SELECT split_part($1::text, ',', 2)::float8 $$;

  -- Stands in for the GiST bbox-overlap operator. Every cell this suite seeds is a point in
  -- disguise, so "does the envelope contain it" is the honest stand-in for "do they overlap".
  CREATE FUNCTION shim_bbox_overlaps(cell geometry, envelope geometry)
  RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
    SELECT split_part($1::text, ',', 1)::float8 >= split_part($2::text, ',', 1)::float8
       AND split_part($1::text, ',', 1)::float8 <= split_part($2::text, ',', 3)::float8
       AND split_part($1::text, ',', 2)::float8 >= split_part($2::text, ',', 2)::float8
       AND split_part($1::text, ',', 2)::float8 <= split_part($2::text, ',', 4)::float8
  $$;

  CREATE OPERATOR && (
    LEFTARG = geometry, RIGHTARG = geometry, FUNCTION = shim_bbox_overlaps
  );
`;

/** The columns the two views read, with the production types. */
const AGRI_SCHEMA = `
  CREATE SCHEMA agri;
  CREATE SCHEMA geo;

  CREATE TABLE agri.data_source (
    id uuid PRIMARY KEY,
    key text NOT NULL,
    name text NOT NULL,
    license_name text,
    allowed_client_exposure boolean NOT NULL DEFAULT false
  );

  CREATE TABLE agri.source_release (
    id uuid PRIMARY KEY,
    data_source_id uuid NOT NULL REFERENCES agri.data_source(id),
    retrieved_at timestamptz NOT NULL
  );

  CREATE TABLE agri.spatial_cell (
    id uuid PRIMARY KEY,
    cell_key text NOT NULL,
    grid_name text NOT NULL,
    resolution_m double precision,
    geometry geometry NOT NULL,
    centroid geometry
  );

  CREATE TABLE agri.signal_observation (
    id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    source_release_id uuid NOT NULL REFERENCES agri.source_release(id),
    cell_id uuid NOT NULL REFERENCES agri.spatial_cell(id),
    signal_name text NOT NULL,
    support_key text NOT NULL DEFAULT 'surface',
    observed_at timestamptz NOT NULL,
    normalized_value double precision,
    normalized_unit text,
    quality_flag text NOT NULL DEFAULT 'accepted',
    coverage_fraction double precision NOT NULL DEFAULT 1,
    is_observed boolean NOT NULL DEFAULT true
  );
`;

/** Applies a committed migration file, honouring drizzle's statement breakpoints. */
async function applyMigration(client: postgres.Sql, file: string): Promise<void> {
  const body = readFileSync(`drizzle/${file}`, "utf8");
  for (const statement of body.split("--> statement-breakpoint")) {
    if (statement.trim() === "") continue;
    await client.unsafe(statement);
  }
}

/** The client-facing signal each warehouse signal_name resolves to, per climate-field.ts. */
function expectedSignalMapping(): Record<string, string> {
  const mapping: Record<string, string> = {};
  for (const signal of CLIMATE_FIELD_SIGNAL_IDS) {
    const definition = CLIMATE_FIELD_SIGNALS[signal];
    if (definition.variants.length === 0) {
      mapping[climateFieldSignalName(signal)] = signal;
      continue;
    }
    for (const variant of definition.variants) {
      mapping[climateFieldSignalName(signal, variant.variant)] = signal;
    }
  }
  return mapping;
}

/** `agri.signal_observation.normalized_unit` for a warehouse signal name. */
function unitForSignalName(signalName: string): string {
  for (const signal of CLIMATE_FIELD_SIGNAL_IDS) {
    const definition = CLIMATE_FIELD_SIGNALS[signal];
    const names =
      definition.variants.length === 0
        ? [climateFieldSignalName(signal)]
        : definition.variants.map((variant) => climateFieldSignalName(signal, variant.variant));
    if (names.includes(signalName)) return definition.unit;
  }
  throw new Error(`no climate signal publishes ${signalName}`);
}

describe.skipIf(ADMIN_DSN === undefined)("climate field reader SQL against PostgreSQL", () => {
  const databaseName = `plantgeo_contract_${randomUUID().replace(/-/g, "").slice(0, 16)}`;
  let admin: postgres.Sql;
  let client: postgres.Sql;

  beforeAll(async () => {
    admin = postgres(ADMIN_DSN as string, { max: 1, onnotice: () => {} });
    // A database of our own, dropped in afterAll: this suite must never be able to touch a
    // table the operator cares about, whatever DSN it is handed.
    await admin.unsafe(`CREATE DATABASE ${databaseName}`);

    const target = new URL(ADMIN_DSN as string);
    target.pathname = `/${databaseName}`;
    client = postgres(target.toString(), { max: 1, onnotice: () => {} });

    // A NEGATIVE session timezone on purpose. `'2026-04-30'::date::timestamptz` resolves
    // through this setting, so a bound written that way lands at 06:00Z while the views label
    // days `AT TIME ZONE 'UTC'` — the disagreement the UTC-pinned bounds exist to remove. A
    // UTC session would hide it completely.
    await client.unsafe(`SET TIME ZONE 'America/Denver'`);
    await client.unsafe(POSTGIS_SHIM);
    await client.unsafe(AGRI_SCHEMA);
    await applyMigration(client, "0019_soil_field_vpd.sql");
    await applyMigration(client, "0020_climate_field.sql");
  }, 60_000);

  afterAll(async () => {
    await client?.end();
    if (admin !== undefined) {
      await admin.unsafe(`DROP DATABASE IF EXISTS ${databaseName} WITH (FORCE)`);
      await admin.end();
    }
  }, 60_000);

  /** Seeds one source, one release and one cell, returning their ids. */
  async function seedLane(options: {
    sourceKey: string;
    gridName: string;
    retrievedAt: string;
    lon: number;
    lat: number;
    cellKey: string;
    allowedClientExposure?: boolean;
  }): Promise<{ sourceId: string; releaseId: string; cellId: string }> {
    const sourceId = randomUUID();
    const releaseId = randomUUID();
    const cellId = randomUUID();
    await client.unsafe(
      `INSERT INTO agri.data_source(id, key, name, license_name, allowed_client_exposure)
       VALUES ($1, $2, $2, 'test-licence', $3)`,
      [sourceId, options.sourceKey, options.allowedClientExposure ?? false]
    );
    await client.unsafe(
      `INSERT INTO agri.source_release(id, data_source_id, retrieved_at) VALUES ($1, $2, $3)`,
      [releaseId, sourceId, options.retrievedAt]
    );
    await client.unsafe(
      `INSERT INTO agri.spatial_cell(id, cell_key, grid_name, resolution_m, geometry, centroid)
       VALUES ($1, $2, $3, 55000, $4, $4)`,
      [cellId, options.cellKey, options.gridName, `${options.lon},${options.lat}`]
    );
    return { sourceId, releaseId, cellId };
  }

  /** A second release of the SAME source: how this lane's duplicates actually arise. */
  async function addRelease(sourceId: string, retrievedAt: string): Promise<string> {
    const releaseId = randomUUID();
    await client.unsafe(
      `INSERT INTO agri.source_release(id, data_source_id, retrieved_at) VALUES ($1, $2, $3)`,
      [releaseId, sourceId, retrievedAt]
    );
    return releaseId;
  }

  async function insertObservation(row: {
    releaseId: string;
    cellId: string;
    signalName: string;
    observedDay: string;
    value: number | null;
    unit?: string;
    supportKey?: string;
    qualityFlag?: string;
    isObserved?: boolean;
  }): Promise<void> {
    await client.unsafe(
      `INSERT INTO agri.signal_observation(
         source_release_id, cell_id, signal_name, support_key, observed_at,
         normalized_value, normalized_unit, quality_flag, is_observed)
       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)`,
      [
        row.releaseId,
        row.cellId,
        row.signalName,
        row.supportKey ?? "surface",
        `${row.observedDay}T00:00:00Z`,
        row.value,
        row.unit ?? unitForSignalName(row.signalName),
        row.qualityFlag ?? "accepted",
        row.isObserved ?? true,
      ]
    );
  }

  /**
   * B1. Both statements have to PARSE before anything else can be asserted, and until this
   * commit neither did: the untyped age bind made the lower bound `date - date`, and casting
   * that integer to timestamptz is 42846. This case fails at `beforeAll` scope if the bind
   * regresses, which is exactly the signal that was missing.
   */
  it("parses and executes both climate statements through the real driver", async () => {
    const cells = await runStatement(
      client,
      climateFieldCellsStatement(BOUNDS, "air_temperature_mean", "C", REQUESTED_DAY)
    );
    expect(Array.isArray(cells)).toBe(true);

    const newest = await runStatement(
      client,
      climateFieldNewestDayStatement(BOUNDS, "air_temperature_mean", "C", REQUESTED_DAY)
    );
    expect(newest).toHaveLength(1);
    expect(newest[0]).toHaveProperty("newest_day");
  });

  // The same defect, same lane-independent cause. The soil field carried it from the day it
  // was written; leaving it untested here is how it survived the first review.
  it("parses and executes both soil-field statements through the real driver", async () => {
    const cells = await runStatement(
      client,
      soilFieldCellsStatement(BOUNDS, "soil_water_content_layer_1", "m^3/m^3", REQUESTED_DAY)
    );
    expect(Array.isArray(cells)).toBe(true);

    const newest = await runStatement(
      client,
      soilFieldNewestDayStatement(BOUNDS, "soil_water_content_layer_1", "m^3/m^3", REQUESTED_DAY)
    );
    expect(newest).toHaveLength(1);
  });

  /**
   * B2. The bug: rows are stamped midnight UTC, so `<= (day + 1)` admitted day+1's own reading
   * and `max()` chose it — the slider on the 29th painted the 30th's field under a caption
   * promising "at or before the 29th".
   */
  it("serves the requested day, never the day after it", async () => {
    const lane = await seedLane({
      sourceKey: "nasa-power-daily",
      gridName: "nasa-power-0.5-degree",
      retrievedAt: "2026-05-01T00:00:00Z",
      lon: -120,
      lat: 45,
      cellKey: "bounds-cell",
      allowedClientExposure: false,
    });
    await insertObservation({
      ...lane,
      signalName: "air_temperature_mean",
      observedDay: REQUESTED_DAY,
      value: 11.5,
    });
    await insertObservation({
      ...lane,
      signalName: "air_temperature_mean",
      observedDay: NEXT_DAY,
      value: 99.9,
    });

    const rows = await runStatement(
      client,
      climateFieldCellsStatement(BOUNDS, "air_temperature_mean", "C", REQUESTED_DAY)
    );
    expect(rows).toHaveLength(1);
    expect(rows[0].observed_day).toBe(REQUESTED_DAY);
    // 99.9 is the next day's reading. Drawing it here is the whole bug.
    expect(Number(rows[0].normalized_value)).toBe(11.5);

    const newest = await runStatement(
      client,
      climateFieldNewestDayStatement(BOUNDS, "air_temperature_mean", "C", REQUESTED_DAY)
    );
    expect(newest[0].newest_day).toBe(REQUESTED_DAY);
  });

  /**
   * B2, second half. The session is `America/Denver`, so the old `date::timestamptz` bound
   * landed at 06:00Z and disagreed with the view's UTC-derived `observed_day` about which day
   * a midnight-stamped row belonged to. The window is the full age in days, inclusive.
   */
  it("pins both window bounds to UTC, not the session timezone", async () => {
    const lane = await seedLane({
      sourceKey: "nasa-power-daily",
      gridName: "nasa-power-0.5-degree",
      retrievedAt: "2026-05-01T00:00:00Z",
      lon: -119,
      lat: 46,
      cellKey: "window-cell",
    });
    // Exactly 30 days before the requested day: the inclusive lower edge.
    await insertObservation({
      ...lane,
      signalName: "wind_speed",
      observedDay: "2026-03-30",
      value: 3.25,
    });

    const atEdge = await runStatement(
      client,
      climateFieldCellsStatement(BOUNDS, "wind_speed", "m/s", "2026-04-29")
    );
    expect(atEdge).toHaveLength(1);
    expect(atEdge[0].observed_day).toBe("2026-03-30");

    // One day further out and the same row is correctly outside the window. Under a
    // session-shifted bound this boundary moves by six hours and the two disagree.
    const pastEdge = await runStatement(
      client,
      climateFieldCellsStatement(BOUNDS, "wind_speed", "m/s", "2026-04-30")
    );
    expect(pastEdge).toHaveLength(0);
  });

  /**
   * S1. `served` reads the base table while `deduped` reads the gated view, so an ungated
   * `served` resolves an instant the view holds nothing at and the whole viewport blanks —
   * while the panel cheerfully reports that rejected row's day as "the newest reading".
   */
  it("ignores rejected, unobserved, null and wrong-unit rows when resolving the day", async () => {
    const lane = await seedLane({
      sourceKey: "nasa-power-daily",
      gridName: "nasa-power-0.5-degree",
      retrievedAt: "2026-05-01T00:00:00Z",
      lon: -121,
      lat: 44,
      cellKey: "gated-cell",
    });
    await insertObservation({
      ...lane,
      signalName: "relative_humidity",
      observedDay: "2026-04-20",
      value: 62,
    });
    // Four newer rows, each excluded by exactly one governed gate. Any one of them reaching
    // `served` moves the resolved instant off 2026-04-20 and blanks the viewport.
    await insertObservation({
      ...lane,
      signalName: "relative_humidity",
      observedDay: "2026-04-21",
      value: 71,
      qualityFlag: "rejected",
    });
    await insertObservation({
      ...lane,
      signalName: "relative_humidity",
      observedDay: "2026-04-22",
      value: 72,
      isObserved: false,
    });
    await insertObservation({
      ...lane,
      signalName: "relative_humidity",
      observedDay: "2026-04-23",
      value: null,
    });
    await insertObservation({
      ...lane,
      signalName: "relative_humidity",
      observedDay: "2026-04-24",
      value: 74,
      unit: "fraction",
    });

    const rows = await runStatement(
      client,
      climateFieldCellsStatement(BOUNDS, "relative_humidity", "%", REQUESTED_DAY)
    );
    expect(rows).toHaveLength(1);
    expect(rows[0].observed_day).toBe("2026-04-20");
    expect(Number(rows[0].normalized_value)).toBe(62);

    const newest = await runStatement(
      client,
      climateFieldNewestDayStatement(BOUNDS, "relative_humidity", "%", REQUESTED_DAY)
    );
    // The panel prints this verbatim. Naming a day the drawing read refuses is a false claim.
    expect(newest[0].newest_day).toBe("2026-04-20");
  });

  /** The duplicate resolution the whole `DISTINCT ON` exists for. */
  it("returns one row per cell, from the newest release", async () => {
    const first = await seedLane({
      sourceKey: "nasa-power-daily",
      gridName: "nasa-power-0.5-degree",
      retrievedAt: "2026-05-01T00:00:00Z",
      lon: -122,
      lat: 47,
      cellKey: "duplicate-cell",
      allowedClientExposure: true,
    });
    // A second, later release of the same source over the same cell and day — an overlapping
    // archive window, which is exactly how the ~47k duplicate keys in production arose.
    const secondRelease = await addRelease(first.sourceId, "2026-06-01T00:00:00Z");
    await insertObservation({
      releaseId: first.releaseId,
      cellId: first.cellId,
      signalName: "precipitation",
      observedDay: REQUESTED_DAY,
      value: 1.1,
    });
    await insertObservation({
      releaseId: secondRelease,
      cellId: first.cellId,
      signalName: "precipitation",
      observedDay: REQUESTED_DAY,
      value: 2.2,
    });

    const rows = await runStatement(
      client,
      climateFieldCellsStatement(BOUNDS, "precipitation", "mm/day", REQUESTED_DAY)
    );
    const forCell = rows.filter((row) => row.cell_key === "duplicate-cell");
    expect(forCell).toHaveLength(1);
    // Newest `retrieved_at` wins: 2026-06-01 beats 2026-05-01.
    expect(Number(forCell[0].normalized_value)).toBe(2.2);
  });

  /** The bbox and the grid predicate both have to exclude, or the lane leaks. */
  it("returns nothing outside the viewport or off the lane's grid", async () => {
    const offGrid = await seedLane({
      sourceKey: "nasa-power-daily",
      gridName: "sentinel2-ndvi-0p25deg",
      retrievedAt: "2026-05-01T00:00:00Z",
      lon: -120.5,
      lat: 45.5,
      cellKey: "off-grid-cell",
    });
    await insertObservation({
      ...offGrid,
      signalName: "dew_point_temperature",
      observedDay: REQUESTED_DAY,
      value: 4,
    });

    const inBounds = await runStatement(
      client,
      climateFieldCellsStatement(BOUNDS, "dew_point_temperature", "C", REQUESTED_DAY)
    );
    expect(inBounds).toHaveLength(0);

    const elsewhere = await runStatement(
      client,
      climateFieldCellsStatement(OUTSIDE_BOUNDS, "air_temperature_mean", "C", REQUESTED_DAY)
    );
    expect(elsewhere).toHaveLength(0);
  });

  /**
   * S2. The view's `signal` column is only worth carrying if it agrees with the resolver the
   * client sends ids through; this pins the two together by behaviour rather than by reading
   * the DDL. It also proves the lane gate: `support_key = 'surface'` does not discriminate,
   * because the ERA5 writer emits that support too.
   */
  it("maps every governed signal_name to the id climate-field.ts resolves it from", async () => {
    const mapping = expectedSignalMapping();
    const lane = await seedLane({
      sourceKey: "nasa-power-daily",
      gridName: "nasa-power-0.5-degree",
      retrievedAt: "2026-05-01T00:00:00Z",
      lon: -118,
      lat: 43,
      cellKey: "mapping-cell",
    });
    for (const signalName of Object.keys(mapping)) {
      await insertObservation({
        ...lane,
        signalName,
        observedDay: "2026-02-02",
        value: 1,
      });
    }

    const rows = (await client.unsafe(
      `SELECT DISTINCT signal_name, signal FROM geo.climate_field_observation
       WHERE cell_id = $1 ORDER BY signal_name`,
      [lane.cellId]
    )) as unknown as { signal_name: string; signal: string }[];

    expect(Object.fromEntries(rows.map((row) => [row.signal_name, row.signal]))).toEqual(mapping);
    // Eleven warehouse signals behind nine client-facing ids.
    expect(rows).toHaveLength(11);
    expect(new Set(rows.map((row) => row.signal)).size).toBe(CLIMATE_FIELD_SIGNAL_IDS.length);
  });

  it("gates the view to its own lane, not merely to the shared support key", async () => {
    // Same support key, same signal name, same grid — a different SOURCE. Before the
    // `source.key` gate this row was served on the climate ramps.
    const impostor = await seedLane({
      sourceKey: "open-meteo-era5-land-archive",
      gridName: "nasa-power-0.5-degree",
      retrievedAt: "2026-05-01T00:00:00Z",
      lon: -117.5,
      lat: 42.5,
      cellKey: "wrong-lane-cell",
    });
    await insertObservation({
      ...impostor,
      signalName: "air_temperature_mean",
      observedDay: "2026-01-05",
      value: -3,
    });

    const served = (await client.unsafe(
      `SELECT count(*)::int AS total FROM geo.climate_field_observation WHERE cell_id = $1`,
      [impostor.cellId]
    )) as unknown as { total: number }[];
    expect(served[0].total).toBe(0);
  });
});
