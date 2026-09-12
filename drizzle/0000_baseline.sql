-- PlantGeo Drizzle greenfield baseline.
--
-- Generated from the current Railway schema by PostgreSQL 18 pg_dump. Regenerate it with
-- `scripts/generate-drizzle-baseline.mjs`; do not hand-transcribe production definitions.
--
-- PREREQUISITES, in order. This baseline creates no extensions, no `agri` objects and no rows:
--   1. CREATE EXTENSION postgis, pgcrypto, vector, btree_gist
--   2. Alembic `upgrade head`, run separately, creates the retained control and lookup schema.
--   3. This baseline.
--   4. `drizzle/seed/` -- this dump is --schema-only, and an empty `geo.layers` makes
--      /api/ready return 503, which fails the Railway healthcheck.
-- `scripts/bootstrap-database.mjs` does 1, 3 and 4, and REFUSES 3 until 2 has been done. It does
-- not run Alembic itself: that is a Python toolchain the Next.js image does not carry.

--
-- PostgreSQL database dump
--



SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: geo; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA IF NOT EXISTS geo;


--
-- Name: public; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA IF NOT EXISTS public;


--
-- Name: SCHEMA public; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON SCHEMA public IS 'standard public schema';


--
-- Name: tracking; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA IF NOT EXISTS tracking;


--
-- Name: intervention_tiles(integer, integer, integer); Type: FUNCTION; Schema: geo; Owner: -
--

CREATE FUNCTION geo.intervention_tiles(z integer, x integer, y integer) RETURNS bytea
    LANGUAGE plpgsql STABLE PARALLEL SAFE
    SET search_path TO 'public', 'pg_catalog'
    AS $$
DECLARE
  bounds_3857 geometry;
  bounds_4326 geometry;
  mvt bytea;
BEGIN
  bounds_3857 := ST_TileEnvelope(z, x, y);
  bounds_4326 := ST_Transform(bounds_3857, 4326);

  SELECT COALESCE(ST_AsMVT(tile, 'interventions', 4096, 'geom'), ''::bytea) INTO mvt
  FROM (
    SELECT
      f.id,
      ST_AsMVTGeom(ST_Transform(f.geom, 3857), bounds_3857, 4096, 64, true) AS geom,
      f.properties ->> 'intervention_type' AS intervention_type,
      f.properties ->> 'priority' AS priority,
      f.properties ->> 'status' AS status,
      f.properties ->> 'name' AS name,
      f.properties ->> 'description' AS description
    FROM geo.features f
    JOIN geo.layers l ON f.layer_id = l.id
    WHERE l.name = 'interventions'
      AND l.is_public IS TRUE
      AND f.status = 'published'
      AND f.geom IS NOT NULL
      AND f.geom && bounds_4326
      AND ST_Intersects(f.geom, bounds_4326)
    LIMIT 10000
  ) AS tile;

  RETURN mvt;
END;
$$;


--
-- Name: sync_feature_geom_from_properties(); Type: FUNCTION; Schema: geo; Owner: -
--

CREATE FUNCTION geo.sync_feature_geom_from_properties() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_catalog'
    AS $$
DECLARE
  parsed geometry;
  repaired geometry;
BEGIN
  IF jsonb_typeof(NEW.properties -> 'geometry') <> 'object' THEN
    NEW.geom := NULL;
    RETURN NEW;
  END IF;

  BEGIN
    parsed := ST_GeomFromGeoJSON((NEW.properties -> 'geometry')::text);
  EXCEPTION WHEN OTHERS THEN
    RAISE EXCEPTION 'geo.features.properties.geometry must be valid GeoJSON: %', SQLERRM
      USING ERRCODE = '22023';
  END;

  IF ST_SRID(parsed) = 0 THEN
    parsed := ST_SetSRID(parsed, 4326);
  END IF;

  IF ST_SRID(parsed) <> 4326 THEN
    RAISE EXCEPTION 'geo.features.properties.geometry must be a valid EPSG:4326 geometry'
      USING ERRCODE = '22023';
  END IF;

  IF NOT ST_IsValid(parsed) THEN
    repaired := ST_MakeValid(parsed);
    IF GeometryType(repaired) = 'GEOMETRYCOLLECTION' THEN
      -- Keep components matching the original topological dimension
      -- (1=point, 2=line, 3=polygon for ST_CollectionExtract).
      repaired := ST_CollectionExtract(repaired, ST_Dimension(parsed) + 1);
    END IF;
    IF repaired IS NULL OR ST_IsEmpty(repaired) OR NOT ST_IsValid(repaired) THEN
      RAISE EXCEPTION 'geo.features.properties.geometry must be a valid EPSG:4326 geometry'
        USING ERRCODE = '22023';
    END IF;
    parsed := repaired;
    -- Web read paths serve properties, not geom: write the repaired geometry
    -- back and flag the repair so callers know the shape changed.
    NEW.properties := jsonb_set(
      jsonb_set(NEW.properties, '{geometry}', ST_AsGeoJSON(parsed)::jsonb),
      '{geometry_repaired}',
      'true'::jsonb
    );
  END IF;

  NEW.geom := parsed;
  RETURN NEW;
END;
$$;


--
-- Name: sync_position_geom_from_metadata(); Type: FUNCTION; Schema: tracking; Owner: -
--

CREATE FUNCTION tracking.sync_position_geom_from_metadata() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
  latitude double precision;
  longitude double precision;
BEGIN
  IF jsonb_typeof(NEW.metadata -> 'lat') = 'number'
    AND jsonb_typeof(NEW.metadata -> 'lon') = 'number' THEN
    latitude := (NEW.metadata ->> 'lat')::double precision;
    longitude := (NEW.metadata ->> 'lon')::double precision;
    IF latitude BETWEEN -90 AND 90 AND longitude BETWEEN -180 AND 180 THEN
      NEW.geom := ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)::geography;
      RETURN NEW;
    END IF;
  END IF;

  NEW.geom := NULL;
  RETURN NEW;
END;
$$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: features; Type: TABLE; Schema: geo; Owner: -
--

CREATE TABLE geo.features (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    layer_id uuid NOT NULL,
    properties jsonb DEFAULT '{}'::jsonb NOT NULL,
    status character varying(20) DEFAULT 'published'::character varying,
    review_note text,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    geom public.geometry(Geometry,4326),
    geometry_id uuid,
    data_available_at timestamp with time zone
);


--
-- Name: geometry; Type: TABLE; Schema: geo; Owner: -
--

CREATE TABLE geo.geometry (
    geometry_id uuid DEFAULT gen_random_uuid() NOT NULL,
    natural_key character varying(255) NOT NULL,
    version_valid_from timestamp with time zone NOT NULL,
    version_valid_to timestamp with time zone,
    geom_kind character varying(16) NOT NULL,
    geom public.geometry(Geometry,4326) NOT NULL,
    centroid public.geometry(Point,4326) NOT NULL,
    grid_name character varying(100),
    cell_key character varying(180),
    resolution_m integer,
    producer character varying(100) NOT NULL,
    superseded_by uuid,
    last_confirmed_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_geometry_cell_fields CHECK (((((geom_kind)::text <> 'grid_cell'::text) AND (grid_name IS NULL) AND (cell_key IS NULL) AND (resolution_m IS NULL)) OR (((geom_kind)::text = 'grid_cell'::text) AND (grid_name IS NOT NULL) AND (cell_key IS NOT NULL) AND (resolution_m IS NOT NULL) AND (resolution_m > 0)))),
    CONSTRAINT ck_geometry_kind CHECK (((geom_kind)::text = ANY (ARRAY[('point'::character varying)::text, ('polygon'::character varying)::text, ('line'::character varying)::text, ('grid_cell'::character varying)::text]))),
    CONSTRAINT ck_geometry_natural_key_namespaced CHECK (((natural_key)::text ~~ ((producer)::text || ':%'::text))),
    CONSTRAINT ck_geometry_supersede CHECK ((((version_valid_to IS NULL) AND (superseded_by IS NULL)) OR ((version_valid_to IS NOT NULL) AND (superseded_by IS NOT NULL)))),
    CONSTRAINT ck_geometry_version_order CHECK (((version_valid_to IS NULL) OR (version_valid_to > version_valid_from)))
);


--
-- Name: geometry_current; Type: VIEW; Schema: geo; Owner: -
--

CREATE VIEW geo.geometry_current AS
 SELECT geometry_id,
    natural_key,
    version_valid_from,
    version_valid_to,
    geom_kind,
    geom,
    centroid,
    grid_name,
    cell_key,
    resolution_m,
    producer,
    superseded_by,
    last_confirmed_at
   FROM geo.geometry
  WHERE (version_valid_to IS NULL);


--
-- Name: layers; Type: TABLE; Schema: geo; Owner: -
--

CREATE TABLE geo.layers (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name character varying(100) NOT NULL,
    type character varying(50) DEFAULT 'vector'::character varying NOT NULL,
    description text,
    style jsonb DEFAULT '{}'::jsonb,
    is_public boolean DEFAULT false,
    min_zoom integer DEFAULT 0,
    max_zoom integer DEFAULT 22,
    team_id uuid,
    sort_order integer DEFAULT 0,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: raster_release; Type: TABLE; Schema: geo; Owner: -
--

CREATE TABLE geo.raster_release (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    collection text NOT NULL,
    property text NOT NULL,
    depth text NOT NULL,
    statistic text NOT NULL,
    source_name text NOT NULL,
    source_release text NOT NULL,
    source_url text NOT NULL,
    license_name text NOT NULL,
    attribution text NOT NULL,
    unit text NOT NULL,
    scale_divisor integer NOT NULL,
    nodata_value double precision,
    value_min double precision,
    value_max double precision,
    color_ramp jsonb NOT NULL,
    object_key text NOT NULL,
    archive_format text NOT NULL,
    checksum_sha256 text NOT NULL,
    size_bytes bigint NOT NULL,
    min_zoom integer NOT NULL,
    max_zoom integer NOT NULL,
    bounds public.geometry(Polygon,4326) NOT NULL,
    published_at timestamp with time zone DEFAULT now() NOT NULL,
    superseded_at timestamp with time zone,
    CONSTRAINT raster_release_checksum_shape CHECK ((checksum_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT raster_release_scale_positive CHECK ((scale_divisor > 0)),
    CONSTRAINT raster_release_zoom_range CHECK (((min_zoom >= 0) AND (max_zoom >= min_zoom)))
);


--
-- Name: TABLE raster_release; Type: COMMENT; Schema: geo; Owner: -
--

COMMENT ON TABLE geo.raster_release IS 'Append-only catalog of first-party raster tile archives on R2. A row is the assertion that a specific set of bytes, with a known checksum and licence, is being served as a map layer. Superseding a release stamps superseded_at and inserts a new row.';


--
-- Name: published_raster; Type: VIEW; Schema: geo; Owner: -
--

CREATE VIEW geo.published_raster AS
 SELECT id,
    collection,
    property,
    depth,
    statistic,
    source_name,
    source_release,
    source_url,
    license_name,
    attribution,
    unit,
    scale_divisor,
    nodata_value,
    value_min,
    value_max,
    color_ramp,
    object_key,
    archive_format,
    checksum_sha256,
    size_bytes,
    min_zoom,
    max_zoom,
    public.st_xmin((bounds)::public.box3d) AS bbox_west,
    public.st_ymin((bounds)::public.box3d) AS bbox_south,
    public.st_xmax((bounds)::public.box3d) AS bbox_east,
    public.st_ymax((bounds)::public.box3d) AS bbox_north,
    published_at
   FROM geo.raster_release release
  WHERE (superseded_at IS NULL);


--
-- Name: VIEW published_raster; Type: COMMENT; Schema: geo; Owner: -
--

COMMENT ON VIEW geo.published_raster IS 'Live raster releases with the bbox flattened to four ordinates. Read by getPublishedRasters; the only supported way to ask what raster layers exist.';


--
-- Name: accounts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.accounts (
    user_id uuid NOT NULL,
    type text NOT NULL,
    provider text NOT NULL,
    provider_account_id text NOT NULL,
    refresh_token text,
    access_token text,
    expires_at integer,
    token_type text,
    scope text,
    id_token text,
    session_state text
);


--
-- Name: agricultural_solutions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agricultural_solutions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name character varying(100) NOT NULL,
    description text,
    suitability_rules jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: ai_conversations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ai_conversations (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    geohash character varying(24) NOT NULL,
    lat double precision NOT NULL,
    lon double precision NOT NULL,
    title character varying(255) DEFAULT 'New Analysis'::character varying NOT NULL,
    message_count integer DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: ai_message_feedback; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ai_message_feedback (
    message_id uuid NOT NULL,
    user_id uuid NOT NULL,
    rating character varying(16) NOT NULL,
    reason character varying(1000),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ai_message_feedback_rating_check CHECK (((rating)::text = ANY ((ARRAY['helpful'::character varying, 'not_helpful'::character varying])::text[])))
);


--
-- Name: ai_messages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ai_messages (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    conversation_id uuid NOT NULL,
    role character varying(10) NOT NULL,
    content text NOT NULL,
    structured_response jsonb,
    token_count integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: alert_subscriptions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.alert_subscriptions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    watched_location_id uuid,
    alert_type character varying(50) NOT NULL,
    threshold jsonb,
    email_enabled boolean DEFAULT true,
    in_app_enabled boolean DEFAULT true,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: api_keys; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.api_keys (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    key_hash text NOT NULL,
    user_id uuid,
    team_id uuid,
    name text,
    permissions jsonb DEFAULT '[]'::jsonb,
    rate_limit integer DEFAULT 1000,
    last_used timestamp with time zone
);


--
-- Name: email_verification_tokens; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.email_verification_tokens (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    token_hash text NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    used_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: environmental_alerts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.environmental_alerts (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    alert_type character varying(50) NOT NULL,
    severity character varying(20) NOT NULL,
    title text NOT NULL,
    body text,
    metadata jsonb,
    is_read boolean DEFAULT false,
    created_at timestamp with time zone DEFAULT now(),
    dedupe_key character varying(160)
);


--
-- Name: open_plant_data; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.open_plant_data (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    scientific_name character varying(200) NOT NULL,
    common_name character varying(200),
    solution_id uuid,
    climate_requirements jsonb DEFAULT '{}'::jsonb,
    water_requirements jsonb DEFAULT '{}'::jsonb,
    soil_requirements jsonb DEFAULT '{}'::jsonb,
    metadata jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: open_tooling_data; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.open_tooling_data (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name character varying(200) NOT NULL,
    solution_id uuid,
    category character varying(100),
    specifications jsonb DEFAULT '{}'::jsonb,
    metadata jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: password_reset_tokens; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.password_reset_tokens (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    token_hash text NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    used_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: priority_zones; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.priority_zones (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    strategy_type character varying(50) NOT NULL,
    request_count integer NOT NULL,
    total_votes integer NOT NULL,
    centroid_lat double precision,
    centroid_lon double precision,
    geojson jsonb,
    computed_at timestamp with time zone DEFAULT now()
);


--
-- Name: request_votes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.request_votes (
    request_id uuid NOT NULL,
    user_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sessions (
    session_token text NOT NULL,
    user_id uuid NOT NULL,
    expires timestamp with time zone NOT NULL
);


--
-- Name: strategy_requests; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.strategy_requests (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid,
    team_id uuid,
    strategy_type character varying(50) NOT NULL,
    title text NOT NULL,
    description text,
    lat double precision NOT NULL,
    lon double precision NOT NULL,
    status character varying(20) DEFAULT 'open'::character varying,
    vote_count integer DEFAULT 0,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: team_invitations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.team_invitations (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    team_id uuid NOT NULL,
    email text NOT NULL,
    team_role character varying(20) DEFAULT 'member'::character varying NOT NULL,
    token_hash text NOT NULL,
    invited_by uuid,
    expires_at timestamp with time zone NOT NULL,
    accepted_at timestamp with time zone,
    accepted_by uuid,
    revoked_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: team_join_links; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.team_join_links (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    team_id uuid NOT NULL,
    code_hash text NOT NULL,
    team_role character varying(20) DEFAULT 'viewer'::character varying NOT NULL,
    allowed_email_domain text,
    max_uses integer,
    use_count integer DEFAULT 0 NOT NULL,
    expires_at timestamp with time zone,
    revoked_at timestamp with time zone,
    created_by uuid,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: team_members; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.team_members (
    team_id uuid NOT NULL,
    user_id uuid NOT NULL,
    team_role character varying(20) DEFAULT 'member'::character varying,
    joined_at timestamp with time zone DEFAULT now()
);


--
-- Name: teams; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.teams (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    slug character varying(100),
    description text,
    org_type character varying(50),
    specialties jsonb,
    website text,
    service_area jsonb,
    is_verified boolean DEFAULT false,
    verified_at timestamp with time zone,
    created_by uuid,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text,
    email text NOT NULL,
    password_hash text,
    platform_role character varying(20) DEFAULT 'contributor'::character varying,
    verified boolean DEFAULT false,
    created_at timestamp with time zone DEFAULT now(),
    email_verified timestamp with time zone,
    image text,
    active_team_id uuid
);


--
-- Name: verification_tokens; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.verification_tokens (
    identifier text NOT NULL,
    token text NOT NULL,
    expires timestamp with time zone NOT NULL
);


--
-- Name: watched_locations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.watched_locations (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    name text NOT NULL,
    lat double precision NOT NULL,
    lon double precision NOT NULL,
    radius_km integer DEFAULT 50,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: alerts; Type: TABLE; Schema: tracking; Owner: -
--

CREATE TABLE tracking.alerts (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    asset_id uuid,
    geofence_id uuid,
    type character varying(50) NOT NULL,
    message text NOT NULL,
    acknowledged boolean DEFAULT false,
    metadata jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: assets; Type: TABLE; Schema: tracking; Owner: -
--

CREATE TABLE tracking.assets (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name character varying(100) NOT NULL,
    type character varying(50) DEFAULT 'vehicle'::character varying,
    status character varying(20) DEFAULT 'offline'::character varying,
    metadata jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: geofences; Type: TABLE; Schema: tracking; Owner: -
--

CREATE TABLE tracking.geofences (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name character varying(100) NOT NULL,
    geometry jsonb DEFAULT '{}'::jsonb NOT NULL,
    alert_on_enter boolean DEFAULT true,
    alert_on_exit boolean DEFAULT true,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: positions; Type: TABLE; Schema: tracking; Owner: -
--

CREATE TABLE tracking.positions (
    "time" timestamp with time zone NOT NULL,
    asset_id uuid NOT NULL,
    heading double precision,
    speed double precision,
    altitude double precision,
    metadata jsonb DEFAULT '{}'::jsonb,
    geom public.geography(Point,4326)
);


--
-- Name: features features_pkey; Type: CONSTRAINT; Schema: geo; Owner: -
--

ALTER TABLE ONLY geo.features
    ADD CONSTRAINT features_pkey PRIMARY KEY (id);


--
-- Name: geometry geometry_pkey; Type: CONSTRAINT; Schema: geo; Owner: -
--

ALTER TABLE ONLY geo.geometry
    ADD CONSTRAINT geometry_pkey PRIMARY KEY (geometry_id);


--
-- Name: layers layers_name_unique; Type: CONSTRAINT; Schema: geo; Owner: -
--

ALTER TABLE ONLY geo.layers
    ADD CONSTRAINT layers_name_unique UNIQUE (name);


--
-- Name: layers layers_pkey; Type: CONSTRAINT; Schema: geo; Owner: -
--

ALTER TABLE ONLY geo.layers
    ADD CONSTRAINT layers_pkey PRIMARY KEY (id);


--
-- Name: raster_release raster_release_pkey; Type: CONSTRAINT; Schema: geo; Owner: -
--

ALTER TABLE ONLY geo.raster_release
    ADD CONSTRAINT raster_release_pkey PRIMARY KEY (id);


--
-- Name: geometry uq_geometry_version; Type: CONSTRAINT; Schema: geo; Owner: -
--

ALTER TABLE ONLY geo.geometry
    ADD CONSTRAINT uq_geometry_version UNIQUE (natural_key, version_valid_from);


--
-- Name: accounts accounts_provider_provider_account_id_pk; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.accounts
    ADD CONSTRAINT accounts_provider_provider_account_id_pk PRIMARY KEY (provider, provider_account_id);


--
-- Name: agricultural_solutions agricultural_solutions_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agricultural_solutions
    ADD CONSTRAINT agricultural_solutions_name_key UNIQUE (name);


--
-- Name: agricultural_solutions agricultural_solutions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agricultural_solutions
    ADD CONSTRAINT agricultural_solutions_pkey PRIMARY KEY (id);


--
-- Name: ai_conversations ai_conversations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_conversations
    ADD CONSTRAINT ai_conversations_pkey PRIMARY KEY (id);


--
-- Name: ai_message_feedback ai_message_feedback_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_message_feedback
    ADD CONSTRAINT ai_message_feedback_pkey PRIMARY KEY (message_id, user_id);


--
-- Name: ai_messages ai_messages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_messages
    ADD CONSTRAINT ai_messages_pkey PRIMARY KEY (id);


--
-- Name: alert_subscriptions alert_subscriptions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alert_subscriptions
    ADD CONSTRAINT alert_subscriptions_pkey PRIMARY KEY (id);


--
-- Name: api_keys api_keys_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_keys
    ADD CONSTRAINT api_keys_pkey PRIMARY KEY (id);


--
-- Name: email_verification_tokens email_verification_tokens_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.email_verification_tokens
    ADD CONSTRAINT email_verification_tokens_pkey PRIMARY KEY (id);


--
-- Name: environmental_alerts environmental_alerts_dedupe_key_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.environmental_alerts
    ADD CONSTRAINT environmental_alerts_dedupe_key_unique UNIQUE (dedupe_key);


--
-- Name: environmental_alerts environmental_alerts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.environmental_alerts
    ADD CONSTRAINT environmental_alerts_pkey PRIMARY KEY (id);


--
-- Name: open_plant_data open_plant_data_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.open_plant_data
    ADD CONSTRAINT open_plant_data_pkey PRIMARY KEY (id);


--
-- Name: open_tooling_data open_tooling_data_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.open_tooling_data
    ADD CONSTRAINT open_tooling_data_pkey PRIMARY KEY (id);


--
-- Name: password_reset_tokens password_reset_tokens_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.password_reset_tokens
    ADD CONSTRAINT password_reset_tokens_pkey PRIMARY KEY (id);


--
-- Name: priority_zones priority_zones_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.priority_zones
    ADD CONSTRAINT priority_zones_pkey PRIMARY KEY (id);


--
-- Name: request_votes request_votes_request_id_user_id_pk; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.request_votes
    ADD CONSTRAINT request_votes_request_id_user_id_pk PRIMARY KEY (request_id, user_id);


--
-- Name: sessions sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sessions
    ADD CONSTRAINT sessions_pkey PRIMARY KEY (session_token);


--
-- Name: strategy_requests strategy_requests_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.strategy_requests
    ADD CONSTRAINT strategy_requests_pkey PRIMARY KEY (id);


--
-- Name: team_invitations team_invitations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.team_invitations
    ADD CONSTRAINT team_invitations_pkey PRIMARY KEY (id);


--
-- Name: team_join_links team_join_links_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.team_join_links
    ADD CONSTRAINT team_join_links_pkey PRIMARY KEY (id);


--
-- Name: team_members team_members_team_id_user_id_pk; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.team_members
    ADD CONSTRAINT team_members_team_id_user_id_pk PRIMARY KEY (team_id, user_id);


--
-- Name: teams teams_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.teams
    ADD CONSTRAINT teams_pkey PRIMARY KEY (id);


--
-- Name: teams teams_slug_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.teams
    ADD CONSTRAINT teams_slug_unique UNIQUE (slug);


--
-- Name: users users_email_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_email_unique UNIQUE (email);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: verification_tokens verification_tokens_identifier_token_pk; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.verification_tokens
    ADD CONSTRAINT verification_tokens_identifier_token_pk PRIMARY KEY (identifier, token);


--
-- Name: watched_locations watched_locations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.watched_locations
    ADD CONSTRAINT watched_locations_pkey PRIMARY KEY (id);


--
-- Name: alerts alerts_pkey; Type: CONSTRAINT; Schema: tracking; Owner: -
--

ALTER TABLE ONLY tracking.alerts
    ADD CONSTRAINT alerts_pkey PRIMARY KEY (id);


--
-- Name: assets assets_pkey; Type: CONSTRAINT; Schema: tracking; Owner: -
--

ALTER TABLE ONLY tracking.assets
    ADD CONSTRAINT assets_pkey PRIMARY KEY (id);


--
-- Name: geofences geofences_pkey; Type: CONSTRAINT; Schema: tracking; Owner: -
--

ALTER TABLE ONLY tracking.geofences
    ADD CONSTRAINT geofences_pkey PRIMARY KEY (id);


--
-- Name: features_layer_external_id_unique; Type: INDEX; Schema: geo; Owner: -
--

CREATE UNIQUE INDEX features_layer_external_id_unique ON geo.features USING btree (layer_id, ((properties ->> 'id'::text))) WHERE (properties ? 'id'::text);


--
-- Name: idx_features_geom; Type: INDEX; Schema: geo; Owner: -
--

CREATE INDEX idx_features_geom ON geo.features USING gist (geom);


--
-- Name: idx_features_layer; Type: INDEX; Schema: geo; Owner: -
--

CREATE INDEX idx_features_layer ON geo.features USING btree (layer_id);


--
-- Name: idx_features_layer_created_at; Type: INDEX; Schema: geo; Owner: -
--

CREATE INDEX idx_features_layer_created_at ON geo.features USING btree (layer_id, created_at);


--
-- Name: idx_features_layer_status; Type: INDEX; Schema: geo; Owner: -
--

CREATE INDEX idx_features_layer_status ON geo.features USING btree (layer_id, status);


--
-- Name: idx_features_layer_updated_at; Type: INDEX; Schema: geo; Owner: -
--

CREATE INDEX idx_features_layer_updated_at ON geo.features USING btree (layer_id, updated_at);


--
-- Name: ix_features_geometry_id; Type: INDEX; Schema: geo; Owner: -
--

CREATE INDEX ix_features_geometry_id ON geo.features USING btree (geometry_id);


--
-- Name: ix_features_layer_geom; Type: INDEX; Schema: geo; Owner: -
--

CREATE INDEX ix_features_layer_geom ON geo.features USING gist (layer_id, geom) WHERE (((status)::text = 'published'::text) AND (geom IS NOT NULL));


--
-- Name: ix_features_updated_at; Type: INDEX; Schema: geo; Owner: -
--

CREATE INDEX ix_features_updated_at ON geo.features USING btree (updated_at DESC);


--
-- Name: ix_geometry_asof; Type: INDEX; Schema: geo; Owner: -
--

CREATE INDEX ix_geometry_asof ON geo.geometry USING btree (natural_key, version_valid_from DESC);


--
-- Name: ix_geometry_kind; Type: INDEX; Schema: geo; Owner: -
--

CREATE INDEX ix_geometry_kind ON geo.geometry USING btree (geom_kind, producer);


--
-- Name: ix_raster_release_live_collection; Type: INDEX; Schema: geo; Owner: -
--

CREATE INDEX ix_raster_release_live_collection ON geo.raster_release USING btree (collection, property) WHERE (superseded_at IS NULL);


--
-- Name: uq_geometry_current; Type: INDEX; Schema: geo; Owner: -
--

CREATE UNIQUE INDEX uq_geometry_current ON geo.geometry USING btree (natural_key) WHERE (version_valid_to IS NULL);


--
-- Name: uq_geometry_grid_cell; Type: INDEX; Schema: geo; Owner: -
--

CREATE UNIQUE INDEX uq_geometry_grid_cell ON geo.geometry USING btree (grid_name, cell_key) WHERE (version_valid_to IS NULL);


--
-- Name: ux_raster_release_live; Type: INDEX; Schema: geo; Owner: -
--

CREATE UNIQUE INDEX ux_raster_release_live ON geo.raster_release USING btree (collection, property, depth, statistic, archive_format) WHERE (superseded_at IS NULL);


--
-- Name: ai_message_feedback_user_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ai_message_feedback_user_idx ON public.ai_message_feedback USING btree (user_id);


--
-- Name: api_keys_key_hash_unique; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX api_keys_key_hash_unique ON public.api_keys USING btree (key_hash);


--
-- Name: email_verification_tokens_token_hash_unique; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX email_verification_tokens_token_hash_unique ON public.email_verification_tokens USING btree (token_hash);


--
-- Name: email_verification_tokens_user_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX email_verification_tokens_user_idx ON public.email_verification_tokens USING btree (user_id);


--
-- Name: password_reset_tokens_token_hash_unique; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX password_reset_tokens_token_hash_unique ON public.password_reset_tokens USING btree (token_hash);


--
-- Name: password_reset_tokens_user_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX password_reset_tokens_user_idx ON public.password_reset_tokens USING btree (user_id);


--
-- Name: team_invitations_email_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX team_invitations_email_idx ON public.team_invitations USING btree (email);


--
-- Name: team_invitations_team_email_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX team_invitations_team_email_idx ON public.team_invitations USING btree (team_id, email);


--
-- Name: team_invitations_token_hash_unique; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX team_invitations_token_hash_unique ON public.team_invitations USING btree (token_hash);


--
-- Name: team_join_links_code_hash_unique; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX team_join_links_code_hash_unique ON public.team_join_links USING btree (code_hash);


--
-- Name: team_join_links_team_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX team_join_links_team_idx ON public.team_join_links USING btree (team_id);


--
-- Name: idx_positions_asset; Type: INDEX; Schema: tracking; Owner: -
--

CREATE INDEX idx_positions_asset ON tracking.positions USING btree (asset_id, "time" DESC);


--
-- Name: idx_positions_geom; Type: INDEX; Schema: tracking; Owner: -
--

CREATE INDEX idx_positions_geom ON tracking.positions USING gist (geom);


--
-- Name: positions_asset_time_unique; Type: INDEX; Schema: tracking; Owner: -
--

CREATE UNIQUE INDEX positions_asset_time_unique ON tracking.positions USING btree (asset_id, "time");


--
-- Name: positions_time_idx; Type: INDEX; Schema: tracking; Owner: -
--

CREATE INDEX positions_time_idx ON tracking.positions USING btree ("time" DESC);


--
-- Name: features geo_features_sync_geom; Type: TRIGGER; Schema: geo; Owner: -
--

CREATE TRIGGER geo_features_sync_geom BEFORE INSERT OR UPDATE OF properties ON geo.features FOR EACH ROW EXECUTE FUNCTION geo.sync_feature_geom_from_properties();


--
-- Name: positions tracking_positions_sync_geom; Type: TRIGGER; Schema: tracking; Owner: -
--

CREATE TRIGGER tracking_positions_sync_geom BEFORE INSERT OR UPDATE OF metadata ON tracking.positions FOR EACH ROW EXECUTE FUNCTION tracking.sync_position_geom_from_metadata();


--
-- Name: features features_geometry_id_fkey; Type: FK CONSTRAINT; Schema: geo; Owner: -
--

ALTER TABLE ONLY geo.features
    ADD CONSTRAINT features_geometry_id_fkey FOREIGN KEY (geometry_id) REFERENCES geo.geometry(geometry_id) ON DELETE RESTRICT;


--
-- Name: features features_layer_id_layers_id_fk; Type: FK CONSTRAINT; Schema: geo; Owner: -
--

ALTER TABLE ONLY geo.features
    ADD CONSTRAINT features_layer_id_layers_id_fk FOREIGN KEY (layer_id) REFERENCES geo.layers(id) ON DELETE CASCADE;


--
-- Name: geometry geometry_superseded_by_fkey; Type: FK CONSTRAINT; Schema: geo; Owner: -
--

ALTER TABLE ONLY geo.geometry
    ADD CONSTRAINT geometry_superseded_by_fkey FOREIGN KEY (superseded_by) REFERENCES geo.geometry(geometry_id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED;


--
-- Name: layers layers_team_id_teams_id_fk; Type: FK CONSTRAINT; Schema: geo; Owner: -
--

ALTER TABLE ONLY geo.layers
    ADD CONSTRAINT layers_team_id_teams_id_fk FOREIGN KEY (team_id) REFERENCES public.teams(id);


--
-- Name: accounts accounts_user_id_users_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.accounts
    ADD CONSTRAINT accounts_user_id_users_id_fk FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: ai_conversations ai_conversations_user_id_users_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_conversations
    ADD CONSTRAINT ai_conversations_user_id_users_id_fk FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: ai_message_feedback ai_message_feedback_message_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_message_feedback
    ADD CONSTRAINT ai_message_feedback_message_id_fkey FOREIGN KEY (message_id) REFERENCES public.ai_messages(id) ON DELETE CASCADE;


--
-- Name: ai_message_feedback ai_message_feedback_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_message_feedback
    ADD CONSTRAINT ai_message_feedback_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: ai_messages ai_messages_conversation_id_ai_conversations_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_messages
    ADD CONSTRAINT ai_messages_conversation_id_ai_conversations_id_fk FOREIGN KEY (conversation_id) REFERENCES public.ai_conversations(id) ON DELETE CASCADE;


--
-- Name: alert_subscriptions alert_subscriptions_user_id_users_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alert_subscriptions
    ADD CONSTRAINT alert_subscriptions_user_id_users_id_fk FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: alert_subscriptions alert_subscriptions_watched_location_id_watched_locations_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alert_subscriptions
    ADD CONSTRAINT alert_subscriptions_watched_location_id_watched_locations_id_fk FOREIGN KEY (watched_location_id) REFERENCES public.watched_locations(id) ON DELETE CASCADE;


--
-- Name: api_keys api_keys_team_id_teams_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_keys
    ADD CONSTRAINT api_keys_team_id_teams_id_fk FOREIGN KEY (team_id) REFERENCES public.teams(id);


--
-- Name: api_keys api_keys_user_id_users_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_keys
    ADD CONSTRAINT api_keys_user_id_users_id_fk FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: email_verification_tokens email_verification_tokens_user_id_users_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.email_verification_tokens
    ADD CONSTRAINT email_verification_tokens_user_id_users_id_fk FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: environmental_alerts environmental_alerts_user_id_users_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.environmental_alerts
    ADD CONSTRAINT environmental_alerts_user_id_users_id_fk FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: open_plant_data open_plant_data_solution_id_agricultural_solutions_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.open_plant_data
    ADD CONSTRAINT open_plant_data_solution_id_agricultural_solutions_id_fk FOREIGN KEY (solution_id) REFERENCES public.agricultural_solutions(id);


--
-- Name: open_tooling_data open_tooling_data_solution_id_agricultural_solutions_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.open_tooling_data
    ADD CONSTRAINT open_tooling_data_solution_id_agricultural_solutions_id_fk FOREIGN KEY (solution_id) REFERENCES public.agricultural_solutions(id);


--
-- Name: password_reset_tokens password_reset_tokens_user_id_users_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.password_reset_tokens
    ADD CONSTRAINT password_reset_tokens_user_id_users_id_fk FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: request_votes request_votes_request_id_strategy_requests_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.request_votes
    ADD CONSTRAINT request_votes_request_id_strategy_requests_id_fk FOREIGN KEY (request_id) REFERENCES public.strategy_requests(id) ON DELETE CASCADE;


--
-- Name: request_votes request_votes_user_id_users_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.request_votes
    ADD CONSTRAINT request_votes_user_id_users_id_fk FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: sessions sessions_user_id_users_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sessions
    ADD CONSTRAINT sessions_user_id_users_id_fk FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: strategy_requests strategy_requests_team_id_teams_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.strategy_requests
    ADD CONSTRAINT strategy_requests_team_id_teams_id_fk FOREIGN KEY (team_id) REFERENCES public.teams(id);


--
-- Name: strategy_requests strategy_requests_user_id_users_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.strategy_requests
    ADD CONSTRAINT strategy_requests_user_id_users_id_fk FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: team_invitations team_invitations_accepted_by_users_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.team_invitations
    ADD CONSTRAINT team_invitations_accepted_by_users_id_fk FOREIGN KEY (accepted_by) REFERENCES public.users(id);


--
-- Name: team_invitations team_invitations_invited_by_users_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.team_invitations
    ADD CONSTRAINT team_invitations_invited_by_users_id_fk FOREIGN KEY (invited_by) REFERENCES public.users(id);


--
-- Name: team_invitations team_invitations_team_id_teams_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.team_invitations
    ADD CONSTRAINT team_invitations_team_id_teams_id_fk FOREIGN KEY (team_id) REFERENCES public.teams(id) ON DELETE CASCADE;


--
-- Name: team_join_links team_join_links_created_by_users_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.team_join_links
    ADD CONSTRAINT team_join_links_created_by_users_id_fk FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: team_join_links team_join_links_team_id_teams_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.team_join_links
    ADD CONSTRAINT team_join_links_team_id_teams_id_fk FOREIGN KEY (team_id) REFERENCES public.teams(id) ON DELETE CASCADE;


--
-- Name: team_members team_members_team_id_teams_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.team_members
    ADD CONSTRAINT team_members_team_id_teams_id_fk FOREIGN KEY (team_id) REFERENCES public.teams(id) ON DELETE CASCADE;


--
-- Name: team_members team_members_user_id_users_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.team_members
    ADD CONSTRAINT team_members_user_id_users_id_fk FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: teams teams_created_by_users_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.teams
    ADD CONSTRAINT teams_created_by_users_id_fk FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: watched_locations watched_locations_user_id_users_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.watched_locations
    ADD CONSTRAINT watched_locations_user_id_users_id_fk FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: positions positions_asset_id_assets_id_fk; Type: FK CONSTRAINT; Schema: tracking; Owner: -
--

ALTER TABLE ONLY tracking.positions
    ADD CONSTRAINT positions_asset_id_assets_id_fk FOREIGN KEY (asset_id) REFERENCES tracking.assets(id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--


