-- PlantGeo relational control and lookup schema.
-- Generated from the current Railway schema after the Parquet cutover.
-- Environmental observations, forecasts, rasters, and derived products do not belong here.

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
-- Name: agri; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA agri;


--
-- Name: expert_label_envelope_valid(jsonb); Type: FUNCTION; Schema: agri; Owner: -
--

CREATE FUNCTION agri.expert_label_envelope_valid(p_envelope jsonb) RETURNS boolean
    LANGUAGE plpgsql IMMUTABLE
    SET "TimeZone" TO 'UTC'
    SET "DateStyle" TO 'ISO, MDY'
    SET extra_float_digits TO '1'
    AS $$
        DECLARE
            numeric_keys constant text[] := ARRAY[
                'mean_annual_precipitation_mm',
                'mean_annual_temperature_c',
                'growing_season_frost_free_days',
                'elevation_m'
            ];
            categorical_keys constant text[] := ARRAY[
                'soil_texture',
                'aridity',
                'usda_hardiness_zone'
            ];
            envelope_key text;
            entry jsonb;
            element jsonb;
        BEGIN
            -- The CHECK-validated key vocabulary for agri.expert_label.condition_envelope.
            -- A harvested envelope term outside this list is a schema change, not data: it
            -- must be added here and mapped onto a governed stream in the same review, so an
            -- unmappable term can never enter the plane looking mapped.
            IF p_envelope IS NULL OR jsonb_typeof(p_envelope) <> 'object' THEN
                RETURN false;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM jsonb_object_keys(p_envelope)) THEN
                RETURN false;
            END IF;

            FOR envelope_key IN SELECT jsonb_object_keys(p_envelope) LOOP
                IF NOT (envelope_key = ANY (numeric_keys) OR envelope_key = ANY (categorical_keys)) THEN
                    RETURN false;
                END IF;
                entry := p_envelope -> envelope_key;

                IF envelope_key = ANY (numeric_keys) THEN
                    -- A numeric term is a point value, a two-element [low, high] range with
                    -- either end open, or an explicit {"min": .., "max": ..} object.
                    IF jsonb_typeof(entry) = 'number' THEN
                        CONTINUE;
                    ELSIF jsonb_typeof(entry) = 'array' THEN
                        IF jsonb_array_length(entry) <> 2 THEN
                            RETURN false;
                        END IF;
                        FOR element IN SELECT jsonb_array_elements(entry) LOOP
                            IF jsonb_typeof(element) NOT IN ('number', 'null') THEN
                                RETURN false;
                            END IF;
                        END LOOP;
                        CONTINUE;
                    ELSIF jsonb_typeof(entry) = 'object' THEN
                        IF EXISTS (
                            SELECT 1
                            FROM jsonb_object_keys(entry) AS bound_key
                            WHERE bound_key NOT IN ('min', 'max')
                        ) THEN
                            RETURN false;
                        END IF;
                        IF NOT (entry ? 'min' OR entry ? 'max') THEN
                            RETURN false;
                        END IF;
                        IF jsonb_typeof(coalesce(entry -> 'min', 'null'::jsonb)) NOT IN ('number', 'null') THEN
                            RETURN false;
                        END IF;
                        IF jsonb_typeof(coalesce(entry -> 'max', 'null'::jsonb)) NOT IN ('number', 'null') THEN
                            RETURN false;
                        END IF;
                        CONTINUE;
                    END IF;
                    RETURN false;
                END IF;

                -- A categorical term is one label or a non-empty list of labels.
                IF jsonb_typeof(entry) = 'string' THEN
                    CONTINUE;
                ELSIF jsonb_typeof(entry) = 'array' THEN
                    IF jsonb_array_length(entry) = 0 THEN
                        RETURN false;
                    END IF;
                    FOR element IN SELECT jsonb_array_elements(entry) LOOP
                        IF jsonb_typeof(element) <> 'string' THEN
                            RETURN false;
                        END IF;
                    END LOOP;
                    CONTINUE;
                END IF;
                RETURN false;
            END LOOP;

            RETURN true;
        END
        $$;


--
-- Name: expert_label_release_summary(character varying); Type: FUNCTION; Schema: agri; Owner: -
--

CREATE FUNCTION agri.expert_label_release_summary(p_release_key character varying) RETURNS TABLE(release_key text, review_tier text, harvest_slice text, label_kind text, review_state text, outcome text, label_count integer, source_count integer, subject_count integer, refuted_count integer, mean_confidence double precision)
    LANGUAGE sql STABLE
    SET "TimeZone" TO 'UTC'
    SET "DateStyle" TO 'ISO, MDY'
    SET extra_float_digits TO '1'
    AS $$
    -- One canonical accounting of a label release, shared by the loader's report, the
    -- owner-signature request document and the serving route's coverage answer. Bounded by
    -- construction: one release key, grouped, stably ordered.
    SELECT
        release.release_key::text,
        release.review_tier::text,
        label.harvest_slice::text,
        label.label_kind::text,
        label.review_state::text,
        label.outcome::text,
        count(*)::integer AS label_count,
        count(DISTINCT label.source_id)::integer AS source_count,
        count(DISTINCT label.subject_normalized)::integer AS subject_count,
        count(*) FILTER (WHERE label.citation_check_refuted)::integer AS refuted_count,
        avg(label.confidence_weight)::double precision AS mean_confidence
    FROM agri.expert_label_release AS release
    JOIN agri.expert_label AS label
      ON label.release_id = release.id
    WHERE release.release_key = p_release_key
    GROUP BY
        release.release_key,
        release.review_tier,
        label.harvest_slice,
        label.label_kind,
        label.review_state,
        label.outcome
    ORDER BY
        label.harvest_slice,
        label.label_kind,
        label.review_state,
        label.outcome
$$;


--
-- Name: guard_expert_label_review_change(); Type: FUNCTION; Schema: agri; Owner: -
--

CREATE FUNCTION agri.guard_expert_label_review_change() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'agri'
    AS $$
        BEGIN
            -- Accident prevention on a NEW plane, not a reinstatement of the enforcement
            -- layer 20260803_0018 retired. That audit's finding was that tamper-evidence is
            -- theatre when the researcher, the DBA and the adversary are one credential; what
            -- it kept was accident prevention (db/AGENTS.md, 'Governance: checksums are
            -- records, not enforcement'). This trigger is exactly that: a reviewed literature
            -- label is the evidence a model was trained on, so an in-place edit after review
            -- would silently invalidate every artifact that cites it. The owner can still
            -- disable this trigger; nothing here claims otherwise.
            IF TG_OP = 'INSERT' THEN
                -- A row is only ever minted at 'draft' (the loader's only INSERT path,
                -- sql/execution/insert_expert_label.sql) or 'rejected' (an automated
                -- citation-check failure recorded straight away). 'agent_reviewed' and
                -- 'approved' are reachable only through the guarded UPDATE transition below --
                -- a bare INSERT can never skip the state machine and mint a trainable or
                -- owner-signed label in one step.
                IF NEW.review_state NOT IN ('draft', 'rejected') THEN
                    RAISE EXCEPTION
                        'expert label % cannot be inserted directly as %; only draft and rejected rows may be inserted, agent_reviewed and approved are reached only by the guarded review transition',
                        NEW.label_key, NEW.review_state;
                END IF;
                RETURN NEW;
            END IF;

            IF TG_OP = 'DELETE' THEN
                IF OLD.review_state <> 'draft' THEN
                    RAISE EXCEPTION
                        'expert label % is % and cannot be deleted; reviewed labels are the evidence a trained artifact cites',
                        OLD.label_key, OLD.review_state;
                END IF;
                RETURN OLD;
            END IF;

            -- Content and lineage are frozen the moment a label leaves draft.
            IF OLD.review_state <> 'draft'
               AND ROW(
                    NEW.id,
                    NEW.label_key,
                    NEW.release_id,
                    NEW.source_id,
                    NEW.label_kind,
                    NEW.subject,
                    NEW.subject_normalized,
                    NEW.outcome,
                    NEW.condition_envelope,
                    NEW.envelope_checksum,
                    NEW.rationale,
                    NEW.supporting_quote,
                    NEW.confidence,
                    NEW.confidence_weight,
                    NEW.harvest_slice,
                    NEW.citation_check_refuted,
                    NEW.citation_check_doi_resolves,
                    NEW.citation_check_reason,
                    NEW.label_checksum,
                    NEW.created_at
               ) IS DISTINCT FROM ROW(
                    OLD.id,
                    OLD.label_key,
                    OLD.release_id,
                    OLD.source_id,
                    OLD.label_kind,
                    OLD.subject,
                    OLD.subject_normalized,
                    OLD.outcome,
                    OLD.condition_envelope,
                    OLD.envelope_checksum,
                    OLD.rationale,
                    OLD.supporting_quote,
                    OLD.confidence,
                    OLD.confidence_weight,
                    OLD.harvest_slice,
                    OLD.citation_check_refuted,
                    OLD.citation_check_doi_resolves,
                    OLD.citation_check_reason,
                    OLD.label_checksum,
                    OLD.created_at
               ) THEN
                RAISE EXCEPTION
                    'expert label % is % and its content and lineage are immutable; harvest a new label instead',
                    OLD.label_key, OLD.review_state;
            END IF;

            IF NEW.review_state IS DISTINCT FROM OLD.review_state THEN
                IF NOT (
                    (OLD.review_state = 'draft' AND NEW.review_state IN ('agent_reviewed', 'rejected'))
                    OR (OLD.review_state = 'agent_reviewed' AND NEW.review_state IN ('approved', 'rejected'))
                ) THEN
                    RAISE EXCEPTION
                        'expert label % cannot move from % to %; the permitted transitions are draft->agent_reviewed, draft->rejected, agent_reviewed->approved, agent_reviewed->rejected',
                        OLD.label_key, OLD.review_state, NEW.review_state;
                END IF;
                IF NEW.review_state = 'agent_reviewed' AND NEW.citation_check_refuted THEN
                    RAISE EXCEPTION
                        'expert label % was refuted by its citation check and can never be agent_reviewed',
                        OLD.label_key;
                END IF;
                IF NEW.review_state = 'approved' AND coalesce(NEW.owner_signature_reference, '') = '' THEN
                    RAISE EXCEPTION
                        'expert label % cannot be approved without an owner signature reference; approval is the owner''s signature, not an agent''s',
                        OLD.label_key;
                END IF;
            END IF;

            RETURN NEW;
        END
        $$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: climate_profiles; Type: TABLE; Schema: agri; Owner: -
--

CREATE TABLE agri.climate_profiles (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    location_id uuid NOT NULL,
    source character varying(10) NOT NULL,
    annual_precip_mm double precision,
    growing_season_days integer,
    avg_temp_c double precision,
    min_temp_c double precision,
    max_temp_c double precision,
    frost_free_days integer,
    koppen_zone character varying(10),
    aridity_index double precision,
    monthly_precip_json jsonb,
    monthly_temp_json jsonb,
    fetched_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_climate_profiles_climate_source CHECK (((source)::text = ANY (ARRAY[('prism'::character varying)::text, ('noaa'::character varying)::text, ('nasa_power'::character varying)::text])))
);


--
-- Name: companion_relationships; Type: TABLE; Schema: agri; Owner: -
--

CREATE TABLE agri.companion_relationships (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    species_a_id uuid NOT NULL,
    species_b_id uuid NOT NULL,
    relationship_type character varying(10) NOT NULL,
    guild_function character varying(100),
    notes text,
    evidence_citation text,
    evidence_source_url character varying(1000),
    evidence_grade character varying(50),
    applicability_context text,
    jurisdiction character varying(255),
    review_state character varying(8) DEFAULT 'draft'::character varying NOT NULL,
    reviewed_at timestamp with time zone,
    reviewed_by character varying(255),
    CONSTRAINT ck_companion_relationships_approved_companion_has_evidence CHECK ((((review_state)::text <> 'approved'::text) OR ((reviewed_at IS NOT NULL) AND (evidence_citation IS NOT NULL)))),
    CONSTRAINT ck_companion_relationships_companion_pair_not_self CHECK ((species_a_id <> species_b_id)),
    CONSTRAINT ck_companion_relationships_companion_relationship_type CHECK (((relationship_type)::text = ANY (ARRAY[('companion'::character varying)::text, ('antagonist'::character varying)::text, ('neutral'::character varying)::text]))),
    CONSTRAINT ck_companion_relationships_companion_review_state CHECK (((review_state)::text = ANY (ARRAY[('draft'::character varying)::text, ('reviewed'::character varying)::text, ('approved'::character varying)::text, ('rejected'::character varying)::text])))
);


--
-- Name: data_source; Type: TABLE; Schema: agri; Owner: -
--

CREATE TABLE agri.data_source (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    key character varying(100) NOT NULL,
    name character varying(255) NOT NULL,
    owner character varying(255) NOT NULL,
    purpose text NOT NULL,
    base_url character varying(1000),
    license_name character varying(255) NOT NULL,
    license_url character varying(1000),
    citation text NOT NULL,
    refresh_policy jsonb DEFAULT '{}'::jsonb NOT NULL,
    retention_days integer,
    allowed_client_exposure boolean DEFAULT false NOT NULL,
    review_state character varying(8) DEFAULT 'draft'::character varying NOT NULL,
    review_due_at timestamp with time zone,
    reviewed_at timestamp with time zone,
    reviewed_by character varying(255),
    is_active boolean DEFAULT true NOT NULL,
    configuration jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_data_source_approved_source_has_review CHECK ((((review_state)::text <> 'approved'::text) OR (reviewed_at IS NOT NULL))),
    CONSTRAINT ck_data_source_positive_retention_days CHECK (((retention_days IS NULL) OR (retention_days > 0))),
    CONSTRAINT ck_data_source_source_review_state CHECK (((review_state)::text = ANY (ARRAY[('draft'::character varying)::text, ('reviewed'::character varying)::text, ('approved'::character varying)::text, ('rejected'::character varying)::text, ('retired'::character varying)::text])))
);


--
-- Name: expert_label; Type: TABLE; Schema: agri; Owner: -
--

CREATE TABLE agri.expert_label (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    label_key character varying(255) NOT NULL,
    release_id uuid NOT NULL,
    source_id uuid NOT NULL,
    label_kind character varying(32) NOT NULL,
    subject character varying(255) NOT NULL,
    subject_normalized character varying(255) NOT NULL,
    outcome character varying(24) NOT NULL,
    condition_envelope jsonb NOT NULL,
    envelope_checksum character varying(64) NOT NULL,
    rationale text NOT NULL,
    supporting_quote text,
    confidence character varying(16) NOT NULL,
    confidence_weight double precision NOT NULL,
    harvest_slice character varying(120) NOT NULL,
    citation_check_refuted boolean NOT NULL,
    citation_check_doi_resolves boolean NOT NULL,
    citation_check_reason text NOT NULL,
    review_state character varying(24) DEFAULT 'draft'::character varying NOT NULL,
    review_note text,
    reviewed_by character varying(255),
    reviewed_at timestamp with time zone,
    owner_signature_reference character varying(500),
    label_checksum character varying(64) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_expert_label_agent_review_not_refuted CHECK ((((review_state)::text <> ALL (ARRAY['agent_reviewed'::text, 'approved'::text])) OR (citation_check_refuted = false))),
    CONSTRAINT ck_expert_label_approval_needs_owner_signature CHECK ((((review_state)::text <> 'approved'::text) OR ((COALESCE(owner_signature_reference, ''::character varying))::text <> ''::text))),
    CONSTRAINT ck_expert_label_checksums CHECK ((((label_checksum)::text ~ '^[0-9a-f]{64}$'::text) AND ((envelope_checksum)::text ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT ck_expert_label_confidence CHECK ((((confidence)::text = ANY (ARRAY[('high'::character varying)::text, ('medium'::character varying)::text, ('low'::character varying)::text])) AND (confidence_weight >= (0.0)::double precision) AND (confidence_weight <= (1.0)::double precision))),
    CONSTRAINT ck_expert_label_envelope CHECK (agri.expert_label_envelope_valid(condition_envelope)),
    CONSTRAINT ck_expert_label_kind CHECK (((label_kind)::text = ANY (ARRAY[('species_fit'::character varying)::text, ('strategy_outcome'::character varying)::text]))),
    CONSTRAINT ck_expert_label_outcome_matches_kind CHECK (((((label_kind)::text = 'species_fit'::text) AND ((outcome)::text = ANY (ARRAY[('fit'::character varying)::text, ('marginal'::character varying)::text, ('unfit'::character varying)::text]))) OR (((label_kind)::text = 'strategy_outcome'::text) AND ((outcome)::text = ANY (ARRAY[('effective'::character varying)::text, ('mixed'::character varying)::text, ('ineffective'::character varying)::text]))))),
    CONSTRAINT ck_expert_label_quote_bounded CHECK (((supporting_quote IS NULL) OR ((char_length(supporting_quote) >= 1) AND (char_length(supporting_quote) <= 1000)))),
    CONSTRAINT ck_expert_label_review_evidence CHECK ((((review_state)::text = 'draft'::text) OR ((reviewed_at IS NOT NULL) AND (reviewed_by IS NOT NULL)))),
    CONSTRAINT ck_expert_label_review_state CHECK (((review_state)::text = ANY (ARRAY[('draft'::character varying)::text, ('agent_reviewed'::character varying)::text, ('approved'::character varying)::text, ('rejected'::character varying)::text])))
);


--
-- Name: expert_label_release; Type: TABLE; Schema: agri; Owner: -
--

CREATE TABLE agri.expert_label_release (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    release_key character varying(255) NOT NULL,
    harvest_document_uri character varying(2000) NOT NULL,
    harvest_document_checksum character varying(64) NOT NULL,
    harvested_at timestamp with time zone NOT NULL,
    label_count integer NOT NULL,
    draft_count integer NOT NULL,
    agent_reviewed_count integer NOT NULL,
    approved_count integer DEFAULT 0 NOT NULL,
    rejected_count integer NOT NULL,
    slice_summary jsonb NOT NULL,
    review_tier character varying(64) DEFAULT 'agent_reviewed_pending_owner_signature'::character varying NOT NULL,
    owner_signature_reference character varying(500),
    owner_signed_at timestamp with time zone,
    release_checksum character varying(64) NOT NULL,
    loader_code_checksum character varying(64) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_expert_label_release_checksums CHECK ((((harvest_document_checksum)::text ~ '^[0-9a-f]{64}$'::text) AND ((release_checksum)::text ~ '^[0-9a-f]{64}$'::text) AND ((loader_code_checksum)::text ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT ck_expert_label_release_counts CHECK (((label_count >= 0) AND (draft_count >= 0) AND (agent_reviewed_count >= 0) AND (approved_count >= 0) AND (rejected_count >= 0) AND (label_count = (((draft_count + agent_reviewed_count) + approved_count) + rejected_count)))),
    CONSTRAINT ck_expert_label_release_owner_signature CHECK ((((review_tier)::text <> 'owner_signed'::text) OR ((owner_signature_reference IS NOT NULL) AND (owner_signed_at IS NOT NULL)))),
    CONSTRAINT ck_expert_label_release_slice_summary CHECK ((jsonb_typeof(slice_summary) = 'object'::text)),
    CONSTRAINT ck_expert_label_release_tier CHECK (((review_tier)::text = ANY (ARRAY[('draft'::character varying)::text, ('agent_reviewed_pending_owner_signature'::character varying)::text, ('owner_signed'::character varying)::text])))
);


--
-- Name: expert_label_source; Type: TABLE; Schema: agri; Owner: -
--

CREATE TABLE agri.expert_label_source (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    source_key character varying(255) NOT NULL,
    doi character varying(255),
    source_url character varying(2000),
    title character varying(1000) NOT NULL,
    publication_year integer NOT NULL,
    journal_or_publisher character varying(500) NOT NULL,
    edition_or_version character varying(255),
    license_posture character varying(120) NOT NULL,
    source_checksum character varying(64) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_expert_label_source_checksum CHECK (((source_checksum)::text ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT ck_expert_label_source_locator CHECK (((doi IS NOT NULL) OR (source_url IS NOT NULL))),
    CONSTRAINT ck_expert_label_source_year CHECK (((publication_year >= 1800) AND (publication_year <= 2100)))
);


--
-- Name: job_attempt; Type: TABLE; Schema: agri; Owner: -
--

CREATE TABLE agri.job_attempt (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    job_work_item_id uuid NOT NULL,
    attempt_number integer NOT NULL,
    fencing_token bigint NOT NULL,
    status character varying(9) DEFAULT 'running'::character varying NOT NULL,
    worker_id character varying(255) NOT NULL,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    heartbeat_at timestamp with time zone,
    finished_at timestamp with time zone,
    failure_class character varying(255),
    error_summary text,
    metrics jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT ck_job_attempt_attempt_state CHECK (((status)::text = ANY (ARRAY[('running'::character varying)::text, ('succeeded'::character varying)::text, ('failed'::character varying)::text, ('lost'::character varying)::text, ('deferred'::character varying)::text, ('cancelled'::character varying)::text]))),
    CONSTRAINT ck_job_attempt_positive_attempt_fencing_token CHECK ((fencing_token > 0)),
    CONSTRAINT ck_job_attempt_positive_attempt_number CHECK ((attempt_number > 0)),
    CONSTRAINT ck_job_attempt_terminal_attempt_has_finish_time CHECK ((((status)::text = 'running'::text) OR (finished_at IS NOT NULL)))
);


--
-- Name: job_checkpoint; Type: TABLE; Schema: agri; Owner: -
--

CREATE TABLE agri.job_checkpoint (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    job_work_item_id uuid NOT NULL,
    job_attempt_id uuid NOT NULL,
    sequence integer NOT NULL,
    fencing_token bigint NOT NULL,
    cursor jsonb NOT NULL,
    cursor_checksum character varying(64) NOT NULL,
    progress_fraction double precision NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_job_checkpoint_checkpoint_progress_fraction_range CHECK (((progress_fraction >= (0)::double precision) AND (progress_fraction <= (1)::double precision))),
    CONSTRAINT ck_job_checkpoint_positive_checkpoint_fencing_token CHECK ((fencing_token > 0)),
    CONSTRAINT ck_job_checkpoint_positive_checkpoint_sequence CHECK ((sequence > 0))
);


--
-- Name: job_definition; Type: TABLE; Schema: agri; Owner: -
--

CREATE TABLE agri.job_definition (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name character varying(150) NOT NULL,
    version character varying(100) NOT NULL,
    handler character varying(500) NOT NULL,
    queue_name character varying(100) DEFAULT 'default'::character varying NOT NULL,
    schedule character varying(255),
    schedule_timezone character varying(100) DEFAULT 'UTC'::character varying NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    concurrency_key character varying(255),
    max_attempts integer DEFAULT 5 NOT NULL,
    lease_seconds integer DEFAULT 300 NOT NULL,
    time_budget_seconds integer DEFAULT 240 NOT NULL,
    retry_policy jsonb DEFAULT '{}'::jsonb NOT NULL,
    parameters jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_job_definition_positive_lease_seconds CHECK ((lease_seconds > 0)),
    CONSTRAINT ck_job_definition_positive_max_attempts CHECK ((max_attempts > 0)),
    CONSTRAINT ck_job_definition_positive_time_budget_seconds CHECK ((time_budget_seconds > 0))
);


--
-- Name: job_dependency; Type: TABLE; Schema: agri; Owner: -
--

CREATE TABLE agri.job_dependency (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    job_run_id uuid NOT NULL,
    depends_on_run_id uuid NOT NULL,
    required_status character varying(32) DEFAULT 'succeeded'::character varying NOT NULL,
    satisfied_at timestamp with time zone,
    CONSTRAINT ck_job_dependency_dependency_not_self CHECK ((job_run_id <> depends_on_run_id)),
    CONSTRAINT ck_job_dependency_dependency_required_status CHECK (((required_status)::text = ANY (ARRAY[('succeeded'::character varying)::text, ('partial'::character varying)::text])))
);


--
-- Name: job_event; Type: TABLE; Schema: agri; Owner: -
--

CREATE TABLE agri.job_event (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    occurred_at timestamp with time zone DEFAULT now() NOT NULL,
    job_run_id uuid,
    job_work_item_id uuid,
    job_attempt_id uuid,
    severity character varying(8) NOT NULL,
    event_code character varying(150) NOT NULL,
    environment character varying(100) NOT NULL,
    service character varying(100) NOT NULL,
    trace_id character varying(255),
    duration_ms bigint,
    progress jsonb DEFAULT '{}'::jsonb NOT NULL,
    detail jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT ck_job_event_event_severity CHECK (((severity)::text = ANY (ARRAY[('debug'::character varying)::text, ('info'::character varying)::text, ('warning'::character varying)::text, ('error'::character varying)::text, ('critical'::character varying)::text]))),
    CONSTRAINT ck_job_event_nonnegative_event_duration CHECK (((duration_ms IS NULL) OR (duration_ms >= 0)))
)
PARTITION BY RANGE (occurred_at);


--
-- Name: job_event_default; Type: TABLE; Schema: agri; Owner: -
--

CREATE TABLE agri.job_event_default (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    occurred_at timestamp with time zone DEFAULT now() NOT NULL,
    job_run_id uuid,
    job_work_item_id uuid,
    job_attempt_id uuid,
    severity character varying(8) NOT NULL,
    event_code character varying(150) NOT NULL,
    environment character varying(100) NOT NULL,
    service character varying(100) NOT NULL,
    trace_id character varying(255),
    duration_ms bigint,
    progress jsonb DEFAULT '{}'::jsonb NOT NULL,
    detail jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT ck_job_event_event_severity CHECK (((severity)::text = ANY (ARRAY[('debug'::character varying)::text, ('info'::character varying)::text, ('warning'::character varying)::text, ('error'::character varying)::text, ('critical'::character varying)::text]))),
    CONSTRAINT ck_job_event_nonnegative_event_duration CHECK (((duration_ms IS NULL) OR (duration_ms >= 0)))
);


--
-- Name: job_incident; Type: TABLE; Schema: agri; Owner: -
--

CREATE TABLE agri.job_incident (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    fingerprint character varying(255) NOT NULL,
    incident_type character varying(150) NOT NULL,
    severity character varying(8) NOT NULL,
    status character varying(12) DEFAULT 'open'::character varying NOT NULL,
    job_run_id uuid,
    job_work_item_id uuid,
    summary text NOT NULL,
    occurrence_count integer DEFAULT 1 NOT NULL,
    first_seen_at timestamp with time zone NOT NULL,
    last_seen_at timestamp with time zone NOT NULL,
    cooldown_until timestamp with time zone,
    owner character varying(255),
    acknowledged_at timestamp with time zone,
    acknowledged_by character varying(255),
    resolved_at timestamp with time zone,
    detail jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_job_incident_acknowledged_incident_has_timestamp CHECK ((((status)::text <> 'acknowledged'::text) OR (acknowledged_at IS NOT NULL))),
    CONSTRAINT ck_job_incident_incident_severity CHECK (((severity)::text = ANY (ARRAY[('debug'::character varying)::text, ('info'::character varying)::text, ('warning'::character varying)::text, ('error'::character varying)::text, ('critical'::character varying)::text]))),
    CONSTRAINT ck_job_incident_incident_state CHECK (((status)::text = ANY (ARRAY[('open'::character varying)::text, ('acknowledged'::character varying)::text, ('resolved'::character varying)::text]))),
    CONSTRAINT ck_job_incident_ordered_incident_seen_window CHECK ((last_seen_at >= first_seen_at)),
    CONSTRAINT ck_job_incident_positive_incident_occurrence_count CHECK ((occurrence_count > 0)),
    CONSTRAINT ck_job_incident_resolved_incident_has_timestamp CHECK ((((status)::text <> 'resolved'::text) OR (resolved_at IS NOT NULL)))
);


--
-- Name: job_outbox; Type: TABLE; Schema: agri; Owner: -
--

CREATE TABLE agri.job_outbox (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    event_key character varying(255) NOT NULL,
    aggregate_type character varying(100) NOT NULL,
    aggregate_id uuid NOT NULL,
    topic character varying(255) NOT NULL,
    payload jsonb NOT NULL,
    status character varying(11) DEFAULT 'pending'::character varying NOT NULL,
    attempt_count integer DEFAULT 0 NOT NULL,
    max_attempts integer DEFAULT 10 NOT NULL,
    next_attempt_at timestamp with time zone DEFAULT now() NOT NULL,
    fencing_token bigint DEFAULT '0'::bigint NOT NULL,
    lease_owner character varying(255),
    lease_expires_at timestamp with time zone,
    delivered_at timestamp with time zone,
    last_error_summary text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_job_outbox_complete_outbox_lease_pair CHECK ((((lease_owner IS NULL) AND (lease_expires_at IS NULL)) OR ((lease_owner IS NOT NULL) AND (lease_expires_at IS NOT NULL)))),
    CONSTRAINT ck_job_outbox_delivered_outbox_has_timestamp CHECK ((((status)::text <> 'delivered'::text) OR (delivered_at IS NOT NULL))),
    CONSTRAINT ck_job_outbox_nonnegative_outbox_attempt_count CHECK ((attempt_count >= 0)),
    CONSTRAINT ck_job_outbox_nonnegative_outbox_fencing_token CHECK ((fencing_token >= 0)),
    CONSTRAINT ck_job_outbox_outbox_attempt_count_within_limit CHECK ((attempt_count <= max_attempts)),
    CONSTRAINT ck_job_outbox_outbox_state CHECK (((status)::text = ANY (ARRAY[('pending'::character varying)::text, ('publishing'::character varying)::text, ('retry_wait'::character varying)::text, ('delivered'::character varying)::text, ('dead_letter'::character varying)::text]))),
    CONSTRAINT ck_job_outbox_positive_outbox_max_attempts CHECK ((max_attempts > 0)),
    CONSTRAINT ck_job_outbox_publishing_outbox_has_fenced_lease CHECK ((((status)::text <> 'publishing'::text) OR ((lease_owner IS NOT NULL) AND (lease_expires_at IS NOT NULL) AND (fencing_token > 0))))
);


--
-- Name: job_output; Type: TABLE; Schema: agri; Owner: -
--

CREATE TABLE agri.job_output (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    job_run_id uuid NOT NULL,
    job_work_item_id uuid,
    job_attempt_id uuid,
    fencing_token bigint,
    artifact_id uuid,
    output_key character varying(500) NOT NULL,
    kind character varying(100) NOT NULL,
    state character varying(10) DEFAULT 'staged'::character varying NOT NULL,
    uri character varying(2000),
    checksum_sha256 character varying(64) NOT NULL,
    row_count bigint,
    size_bytes bigint,
    metadata_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    validated_at timestamp with time zone,
    CONSTRAINT ck_job_output_nonnegative_output_rows CHECK (((row_count IS NULL) OR (row_count >= 0))),
    CONSTRAINT ck_job_output_nonnegative_output_size CHECK (((size_bytes IS NULL) OR (size_bytes >= 0))),
    CONSTRAINT ck_job_output_output_state CHECK (((state)::text = ANY (ARRAY[('staged'::character varying)::text, ('validated'::character varying)::text, ('rejected'::character varying)::text, ('published'::character varying)::text, ('superseded'::character varying)::text]))),
    CONSTRAINT ck_job_output_validated_output_has_timestamp CHECK ((((state)::text <> ALL (ARRAY[('validated'::character varying)::text, ('published'::character varying)::text])) OR (validated_at IS NOT NULL))),
    CONSTRAINT ck_job_output_work_output_has_attempt_fence CHECK ((((job_work_item_id IS NULL) AND (job_attempt_id IS NULL) AND (fencing_token IS NULL)) OR ((job_work_item_id IS NOT NULL) AND (job_attempt_id IS NOT NULL) AND (fencing_token IS NOT NULL) AND (fencing_token > 0))))
);


--
-- Name: job_run; Type: TABLE; Schema: agri; Owner: -
--

CREATE TABLE agri.job_run (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    job_definition_id uuid NOT NULL,
    release_set_id uuid,
    logical_run_key character varying(255) NOT NULL,
    scheduled_for timestamp with time zone NOT NULL,
    recipe_version character varying(100),
    model_version character varying(100),
    target_partitions jsonb DEFAULT '{}'::jsonb NOT NULL,
    status character varying(11) DEFAULT 'queued'::character varying NOT NULL,
    requested_by character varying(255),
    total_work_items integer DEFAULT 0 NOT NULL,
    succeeded_work_items integer DEFAULT 0 NOT NULL,
    failed_work_items integer DEFAULT 0 NOT NULL,
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    cancellation_reason text,
    last_error_summary text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_job_run_job_run_state CHECK (((status)::text = ANY (ARRAY[('queued'::character varying)::text, ('running'::character varying)::text, ('succeeded'::character varying)::text, ('partial'::character varying)::text, ('failed'::character varying)::text, ('dead_letter'::character varying)::text, ('cancelled'::character varying)::text]))),
    CONSTRAINT ck_job_run_nonnegative_failed_work_items CHECK ((failed_work_items >= 0)),
    CONSTRAINT ck_job_run_nonnegative_succeeded_work_items CHECK ((succeeded_work_items >= 0)),
    CONSTRAINT ck_job_run_nonnegative_total_work_items CHECK ((total_work_items >= 0)),
    CONSTRAINT ck_job_run_terminal_run_has_completion_time CHECK ((((status)::text <> ALL (ARRAY[('succeeded'::character varying)::text, ('partial'::character varying)::text, ('failed'::character varying)::text, ('dead_letter'::character varying)::text, ('cancelled'::character varying)::text])) OR (completed_at IS NOT NULL))),
    CONSTRAINT ck_job_run_work_item_counts_within_total CHECK (((succeeded_work_items + failed_work_items) <= total_work_items))
);


--
-- Name: job_work_item; Type: TABLE; Schema: agri; Owner: -
--

CREATE TABLE agri.job_work_item (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    job_run_id uuid NOT NULL,
    shard_key character varying(500) NOT NULL,
    kind character varying(100) NOT NULL,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    status character varying(11) DEFAULT 'queued'::character varying NOT NULL,
    priority integer DEFAULT 0 NOT NULL,
    available_at timestamp with time zone DEFAULT now() NOT NULL,
    next_attempt_at timestamp with time zone,
    attempt_count integer DEFAULT 0 NOT NULL,
    max_attempts integer DEFAULT 5 NOT NULL,
    fencing_token bigint DEFAULT '0'::bigint NOT NULL,
    lease_owner character varying(255),
    lease_expires_at timestamp with time zone,
    heartbeat_at timestamp with time zone,
    progress_fraction double precision DEFAULT '0'::double precision NOT NULL,
    checkpoint_sequence integer DEFAULT 0 NOT NULL,
    last_error_class character varying(255),
    last_error_summary text,
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_job_work_item_active_item_has_fenced_lease CHECK ((((status)::text <> ALL (ARRAY[('leased'::character varying)::text, ('running'::character varying)::text])) OR ((lease_owner IS NOT NULL) AND (lease_expires_at IS NOT NULL) AND (fencing_token > 0)))),
    CONSTRAINT ck_job_work_item_attempt_count_within_limit CHECK ((attempt_count <= max_attempts)),
    CONSTRAINT ck_job_work_item_complete_lease_pair CHECK ((((lease_owner IS NULL) AND (lease_expires_at IS NULL)) OR ((lease_owner IS NOT NULL) AND (lease_expires_at IS NOT NULL)))),
    CONSTRAINT ck_job_work_item_nonnegative_attempt_count CHECK ((attempt_count >= 0)),
    CONSTRAINT ck_job_work_item_nonnegative_checkpoint_sequence CHECK ((checkpoint_sequence >= 0)),
    CONSTRAINT ck_job_work_item_nonnegative_fencing_token CHECK ((fencing_token >= 0)),
    CONSTRAINT ck_job_work_item_positive_work_item_max_attempts CHECK ((max_attempts > 0)),
    CONSTRAINT ck_job_work_item_progress_fraction_range CHECK (((progress_fraction >= (0)::double precision) AND (progress_fraction <= (1)::double precision))),
    CONSTRAINT ck_job_work_item_resumable_item_has_next_attempt CHECK ((((status)::text <> ALL (ARRAY[('retry_wait'::character varying)::text, ('deferred'::character varying)::text])) OR (next_attempt_at IS NOT NULL))),
    CONSTRAINT ck_job_work_item_terminal_item_has_completion_time CHECK ((((status)::text <> ALL (ARRAY[('succeeded'::character varying)::text, ('dead_letter'::character varying)::text, ('cancelled'::character varying)::text])) OR (completed_at IS NOT NULL))),
    CONSTRAINT ck_job_work_item_work_item_state CHECK (((status)::text = ANY (ARRAY[('queued'::character varying)::text, ('leased'::character varying)::text, ('running'::character varying)::text, ('retry_wait'::character varying)::text, ('deferred'::character varying)::text, ('succeeded'::character varying)::text, ('dead_letter'::character varying)::text, ('cancelled'::character varying)::text])))
);


--
-- Name: locations; Type: TABLE; Schema: agri; Owner: -
--

CREATE TABLE agri.locations (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name character varying(255) NOT NULL,
    geometry public.geometry(Point,4326) NOT NULL,
    bounding_box public.geometry(Polygon,4326),
    usda_zone character varying(10),
    epa_ecoregion character varying(100),
    elevation_m double precision,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: soil_profiles; Type: TABLE; Schema: agri; Owner: -
--

CREATE TABLE agri.soil_profiles (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    location_id uuid NOT NULL,
    source character varying(9) NOT NULL,
    soil_type character varying(100),
    texture_class character varying(50),
    ph double precision,
    organic_matter_pct double precision,
    cec double precision,
    bulk_density double precision,
    drainage_class character varying(50),
    depth_cm double precision,
    sand_pct double precision,
    silt_pct double precision,
    clay_pct double precision,
    available_water_capacity double precision,
    fetched_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_soil_profiles_soil_source CHECK (((source)::text = ANY (ARRAY[('ssurgo'::character varying)::text, ('soilgrids'::character varying)::text])))
);


--
-- Name: spatial_cell; Type: TABLE; Schema: agri; Owner: -
--

CREATE TABLE agri.spatial_cell (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    cell_key character varying(180) NOT NULL,
    grid_name character varying(100) NOT NULL,
    resolution_m integer NOT NULL,
    geometry public.geometry(Polygon,4326) NOT NULL,
    centroid public.geometry(Point,4326) NOT NULL,
    parent_cell_id uuid,
    coverage_fraction double precision DEFAULT '1'::double precision NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_spatial_cell_positive_resolution CHECK ((resolution_m > 0)),
    CONSTRAINT ck_spatial_cell_valid_coverage CHECK (((coverage_fraction > (0)::double precision) AND (coverage_fraction <= (1)::double precision)))
);


--
-- Name: species; Type: TABLE; Schema: agri; Owner: -
--

CREATE TABLE agri.species (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    scientific_name character varying(255) NOT NULL,
    common_name character varying(255) NOT NULL,
    usda_symbol character varying(20),
    family character varying(100),
    growth_habit character varying(50),
    native_status character varying(50),
    usda_zones int4range,
    min_precip_mm double precision,
    max_precip_mm double precision,
    min_ph double precision,
    max_ph double precision,
    light_requirement character varying(50),
    drought_tolerance character varying(20),
    salt_tolerance character varying(20),
    nitrogen_fixer boolean NOT NULL,
    pollinator_value character varying(6),
    edible boolean NOT NULL,
    timber_value boolean NOT NULL,
    guild_roles character varying[],
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_species_pollinator_value CHECK (((pollinator_value)::text = ANY (ARRAY[('none'::character varying)::text, ('low'::character varying)::text, ('medium'::character varying)::text, ('high'::character varying)::text])))
);


--
-- Name: strategies; Type: TABLE; Schema: agri; Owner: -
--

CREATE TABLE agri.strategies (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name character varying(255) NOT NULL,
    slug character varying(255) NOT NULL,
    category character varying(100) NOT NULL,
    authority character varying(100) NOT NULL,
    practice_code character varying(100) NOT NULL,
    description text NOT NULL,
    min_precip_mm double precision,
    max_precip_mm double precision,
    min_temp_c double precision,
    max_temp_c double precision,
    suitable_soil_types character varying[],
    suitable_drainage character varying[],
    max_slope_pct double precision,
    min_organic_matter_pct double precision,
    water_requirement character varying(6),
    labor_intensity character varying(6),
    time_to_yield_years double precision,
    carbon_seq_potential character varying(9),
    biodiversity_impact character varying(9),
    evidence_citation text,
    evidence_source_url character varying(1000),
    jurisdiction character varying(255),
    limitations text,
    review_state character varying(8) DEFAULT 'draft'::character varying NOT NULL,
    reviewed_at timestamp with time zone,
    reviewed_by character varying(255),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_strategies_approved_strategy_has_evidence CHECK ((((review_state)::text <> 'approved'::text) OR ((reviewed_at IS NOT NULL) AND (reviewed_by IS NOT NULL) AND (evidence_citation IS NOT NULL) AND (evidence_source_url IS NOT NULL) AND (jurisdiction IS NOT NULL) AND (limitations IS NOT NULL)))),
    CONSTRAINT ck_strategies_labor_intensity CHECK (((labor_intensity)::text = ANY (ARRAY[('low'::character varying)::text, ('medium'::character varying)::text, ('high'::character varying)::text]))),
    CONSTRAINT ck_strategies_strategy_biodiversity_impact_level CHECK (((biodiversity_impact)::text = ANY (ARRAY[('low'::character varying)::text, ('medium'::character varying)::text, ('high'::character varying)::text, ('very_high'::character varying)::text]))),
    CONSTRAINT ck_strategies_strategy_carbon_impact_level CHECK (((carbon_seq_potential)::text = ANY (ARRAY[('low'::character varying)::text, ('medium'::character varying)::text, ('high'::character varying)::text, ('very_high'::character varying)::text]))),
    CONSTRAINT ck_strategies_strategy_review_state CHECK (((review_state)::text = ANY (ARRAY[('draft'::character varying)::text, ('reviewed'::character varying)::text, ('approved'::character varying)::text, ('rejected'::character varying)::text]))),
    CONSTRAINT ck_strategies_water_requirement CHECK (((water_requirement)::text = ANY (ARRAY[('low'::character varying)::text, ('medium'::character varying)::text, ('high'::character varying)::text])))
);


--
-- Name: topography_profiles; Type: TABLE; Schema: agri; Owner: -
--

CREATE TABLE agri.topography_profiles (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    location_id uuid NOT NULL,
    elevation_m double precision,
    slope_pct double precision,
    aspect_deg double precision,
    curvature double precision,
    twi double precision,
    fetched_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: water_profiles; Type: TABLE; Schema: agri; Owner: -
--

CREATE TABLE agri.water_profiles (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    location_id uuid NOT NULL,
    source character varying(12) NOT NULL,
    nearest_stream_distance_m double precision,
    watershed_huc12 character varying(12),
    annual_runoff_mm double precision,
    flood_zone character varying(20),
    groundwater_depth_m double precision,
    water_table_seasonal_json jsonb,
    fetched_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_water_profiles_water_source CHECK (((source)::text = ANY (ARRAY[('usgs_nwis'::character varying)::text, ('noaa_atlas14'::character varying)::text])))
);


--
-- Name: job_event_default; Type: TABLE ATTACH; Schema: agri; Owner: -
--

ALTER TABLE ONLY agri.job_event ATTACH PARTITION agri.job_event_default DEFAULT;


--
-- Name: job_event pk_job_event; Type: CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE ONLY agri.job_event
    ADD CONSTRAINT pk_job_event PRIMARY KEY (id, occurred_at);


--
-- Name: job_event_default job_event_default_pkey; Type: CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE ONLY agri.job_event_default
    ADD CONSTRAINT job_event_default_pkey PRIMARY KEY (id, occurred_at);


--
-- Name: climate_profiles pk_climate_profiles; Type: CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE ONLY agri.climate_profiles
    ADD CONSTRAINT pk_climate_profiles PRIMARY KEY (id);


--
-- Name: companion_relationships pk_companion_relationships; Type: CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE ONLY agri.companion_relationships
    ADD CONSTRAINT pk_companion_relationships PRIMARY KEY (id);


--
-- Name: data_source pk_data_source; Type: CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE ONLY agri.data_source
    ADD CONSTRAINT pk_data_source PRIMARY KEY (id);


--
-- Name: expert_label pk_expert_label; Type: CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE ONLY agri.expert_label
    ADD CONSTRAINT pk_expert_label PRIMARY KEY (id);


--
-- Name: expert_label_release pk_expert_label_release; Type: CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE ONLY agri.expert_label_release
    ADD CONSTRAINT pk_expert_label_release PRIMARY KEY (id);


--
-- Name: expert_label_source pk_expert_label_source; Type: CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE ONLY agri.expert_label_source
    ADD CONSTRAINT pk_expert_label_source PRIMARY KEY (id);


--
-- Name: job_attempt pk_job_attempt; Type: CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE ONLY agri.job_attempt
    ADD CONSTRAINT pk_job_attempt PRIMARY KEY (id);


--
-- Name: job_checkpoint pk_job_checkpoint; Type: CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE ONLY agri.job_checkpoint
    ADD CONSTRAINT pk_job_checkpoint PRIMARY KEY (id);


--
-- Name: job_definition pk_job_definition; Type: CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE ONLY agri.job_definition
    ADD CONSTRAINT pk_job_definition PRIMARY KEY (id);


--
-- Name: job_dependency pk_job_dependency; Type: CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE ONLY agri.job_dependency
    ADD CONSTRAINT pk_job_dependency PRIMARY KEY (id);


--
-- Name: job_incident pk_job_incident; Type: CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE ONLY agri.job_incident
    ADD CONSTRAINT pk_job_incident PRIMARY KEY (id);


--
-- Name: job_outbox pk_job_outbox; Type: CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE ONLY agri.job_outbox
    ADD CONSTRAINT pk_job_outbox PRIMARY KEY (id);


--
-- Name: job_output pk_job_output; Type: CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE ONLY agri.job_output
    ADD CONSTRAINT pk_job_output PRIMARY KEY (id);


--
-- Name: job_run pk_job_run; Type: CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE ONLY agri.job_run
    ADD CONSTRAINT pk_job_run PRIMARY KEY (id);


--
-- Name: job_work_item pk_job_work_item; Type: CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE ONLY agri.job_work_item
    ADD CONSTRAINT pk_job_work_item PRIMARY KEY (id);


--
-- Name: locations pk_locations; Type: CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE ONLY agri.locations
    ADD CONSTRAINT pk_locations PRIMARY KEY (id);


--
-- Name: soil_profiles pk_soil_profiles; Type: CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE ONLY agri.soil_profiles
    ADD CONSTRAINT pk_soil_profiles PRIMARY KEY (id);


--
-- Name: spatial_cell pk_spatial_cell; Type: CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE ONLY agri.spatial_cell
    ADD CONSTRAINT pk_spatial_cell PRIMARY KEY (id);


--
-- Name: species pk_species; Type: CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE ONLY agri.species
    ADD CONSTRAINT pk_species PRIMARY KEY (id);


--
-- Name: strategies pk_strategies; Type: CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE ONLY agri.strategies
    ADD CONSTRAINT pk_strategies PRIMARY KEY (id);


--
-- Name: topography_profiles pk_topography_profiles; Type: CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE ONLY agri.topography_profiles
    ADD CONSTRAINT pk_topography_profiles PRIMARY KEY (id);


--
-- Name: water_profiles pk_water_profiles; Type: CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE ONLY agri.water_profiles
    ADD CONSTRAINT pk_water_profiles PRIMARY KEY (id);


--
-- Name: companion_relationships uq_companion_pair; Type: CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE ONLY agri.companion_relationships
    ADD CONSTRAINT uq_companion_pair UNIQUE (species_a_id, species_b_id);


--
-- Name: data_source uq_data_source_key; Type: CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE ONLY agri.data_source
    ADD CONSTRAINT uq_data_source_key UNIQUE (key);


--
-- Name: expert_label uq_expert_label_key; Type: CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE ONLY agri.expert_label
    ADD CONSTRAINT uq_expert_label_key UNIQUE (label_key);


--
-- Name: expert_label_release uq_expert_label_release_key; Type: CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE ONLY agri.expert_label_release
    ADD CONSTRAINT uq_expert_label_release_key UNIQUE (release_key);


--
-- Name: expert_label_source uq_expert_label_source_key; Type: CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE ONLY agri.expert_label_source
    ADD CONSTRAINT uq_expert_label_source_key UNIQUE (source_key);


--
-- Name: job_attempt uq_job_attempt_checkpoint_fence; Type: CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE ONLY agri.job_attempt
    ADD CONSTRAINT uq_job_attempt_checkpoint_fence UNIQUE (id, job_work_item_id, fencing_token);


--
-- Name: job_attempt uq_job_attempt_item_fence; Type: CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE ONLY agri.job_attempt
    ADD CONSTRAINT uq_job_attempt_item_fence UNIQUE (job_work_item_id, fencing_token);


--
-- Name: job_attempt uq_job_attempt_item_number; Type: CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE ONLY agri.job_attempt
    ADD CONSTRAINT uq_job_attempt_item_number UNIQUE (job_work_item_id, attempt_number);


--
-- Name: job_checkpoint uq_job_checkpoint_item_sequence; Type: CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE ONLY agri.job_checkpoint
    ADD CONSTRAINT uq_job_checkpoint_item_sequence UNIQUE (job_work_item_id, sequence);


--
-- Name: job_definition uq_job_definition_name_version; Type: CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE ONLY agri.job_definition
    ADD CONSTRAINT uq_job_definition_name_version UNIQUE (name, version);


--
-- Name: job_dependency uq_job_dependency_edge; Type: CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE ONLY agri.job_dependency
    ADD CONSTRAINT uq_job_dependency_edge UNIQUE (job_run_id, depends_on_run_id);


--
-- Name: job_incident uq_job_incident_fingerprint; Type: CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE ONLY agri.job_incident
    ADD CONSTRAINT uq_job_incident_fingerprint UNIQUE (fingerprint);


--
-- Name: job_outbox uq_job_outbox_event_key; Type: CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE ONLY agri.job_outbox
    ADD CONSTRAINT uq_job_outbox_event_key UNIQUE (event_key);


--
-- Name: job_output uq_job_output_run_key; Type: CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE ONLY agri.job_output
    ADD CONSTRAINT uq_job_output_run_key UNIQUE (job_run_id, output_key);


--
-- Name: job_run uq_job_run_logical_run_key; Type: CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE ONLY agri.job_run
    ADD CONSTRAINT uq_job_run_logical_run_key UNIQUE (logical_run_key);


--
-- Name: job_work_item uq_job_work_item_run_identity; Type: CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE ONLY agri.job_work_item
    ADD CONSTRAINT uq_job_work_item_run_identity UNIQUE (id, job_run_id);


--
-- Name: job_work_item uq_job_work_item_run_shard; Type: CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE ONLY agri.job_work_item
    ADD CONSTRAINT uq_job_work_item_run_shard UNIQUE (job_run_id, shard_key);


--
-- Name: spatial_cell uq_spatial_cell_cell_key; Type: CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE ONLY agri.spatial_cell
    ADD CONSTRAINT uq_spatial_cell_cell_key UNIQUE (cell_key);


--
-- Name: species uq_species_scientific_name; Type: CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE ONLY agri.species
    ADD CONSTRAINT uq_species_scientific_name UNIQUE (scientific_name);


--
-- Name: strategies uq_strategies_name; Type: CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE ONLY agri.strategies
    ADD CONSTRAINT uq_strategies_name UNIQUE (name);


--
-- Name: strategies uq_strategies_slug; Type: CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE ONLY agri.strategies
    ADD CONSTRAINT uq_strategies_slug UNIQUE (slug);


--
-- Name: strategies uq_strategy_authority_practice; Type: CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE ONLY agri.strategies
    ADD CONSTRAINT uq_strategy_authority_practice UNIQUE (authority, practice_code);


--
-- Name: ix_climate_profiles_location_id; Type: INDEX; Schema: agri; Owner: -
--

CREATE INDEX ix_climate_profiles_location_id ON agri.climate_profiles USING btree (location_id);


--
-- Name: ix_companion_relationships_species_a_id; Type: INDEX; Schema: agri; Owner: -
--

CREATE INDEX ix_companion_relationships_species_a_id ON agri.companion_relationships USING btree (species_a_id);


--
-- Name: ix_companion_relationships_species_b_id; Type: INDEX; Schema: agri; Owner: -
--

CREATE INDEX ix_companion_relationships_species_b_id ON agri.companion_relationships USING btree (species_b_id);


--
-- Name: ix_expert_label_release_state; Type: INDEX; Schema: agri; Owner: -
--

CREATE INDEX ix_expert_label_release_state ON agri.expert_label USING btree (release_id, review_state);


--
-- Name: ix_expert_label_source_doi; Type: INDEX; Schema: agri; Owner: -
--

CREATE INDEX ix_expert_label_source_doi ON agri.expert_label_source USING btree (doi) WHERE (doi IS NOT NULL);


--
-- Name: ix_expert_label_trainable; Type: INDEX; Schema: agri; Owner: -
--

CREATE INDEX ix_expert_label_trainable ON agri.expert_label USING btree (review_state, label_kind, subject_normalized);


--
-- Name: ix_job_event_run_occurred; Type: INDEX; Schema: agri; Owner: -
--

CREATE INDEX ix_job_event_run_occurred ON ONLY agri.job_event USING btree (job_run_id, occurred_at);


--
-- Name: ix_job_event_severity_occurred; Type: INDEX; Schema: agri; Owner: -
--

CREATE INDEX ix_job_event_severity_occurred ON ONLY agri.job_event USING btree (severity, occurred_at);


--
-- Name: ix_job_incident_status_severity; Type: INDEX; Schema: agri; Owner: -
--

CREATE INDEX ix_job_incident_status_severity ON agri.job_incident USING btree (status, severity);


--
-- Name: ix_job_outbox_dispatch; Type: INDEX; Schema: agri; Owner: -
--

CREATE INDEX ix_job_outbox_dispatch ON agri.job_outbox USING btree (status, next_attempt_at);


--
-- Name: ix_job_output_artifact_id; Type: INDEX; Schema: agri; Owner: -
--

CREATE INDEX ix_job_output_artifact_id ON agri.job_output USING btree (artifact_id);


--
-- Name: ix_job_run_created_at; Type: INDEX; Schema: agri; Owner: -
--

CREATE INDEX ix_job_run_created_at ON agri.job_run USING btree (created_at DESC);


--
-- Name: ix_job_run_definition_created; Type: INDEX; Schema: agri; Owner: -
--

CREATE INDEX ix_job_run_definition_created ON agri.job_run USING btree (job_definition_id, created_at DESC);


--
-- Name: ix_job_run_status_scheduled; Type: INDEX; Schema: agri; Owner: -
--

CREATE INDEX ix_job_run_status_scheduled ON agri.job_run USING btree (status, scheduled_for);


--
-- Name: ix_job_work_item_claim; Type: INDEX; Schema: agri; Owner: -
--

CREATE INDEX ix_job_work_item_claim ON agri.job_work_item USING btree (status, next_attempt_at, available_at, priority);


--
-- Name: ix_job_work_item_lease_expiry; Type: INDEX; Schema: agri; Owner: -
--

CREATE INDEX ix_job_work_item_lease_expiry ON agri.job_work_item USING btree (lease_expires_at);


--
-- Name: ix_job_work_item_reopened_gaps; Type: INDEX; Schema: agri; Owner: -
--

CREATE INDEX ix_job_work_item_reopened_gaps ON agri.job_work_item USING btree (((payload -> 'reopened_from_observed_gaps'::text))) WHERE ((payload -> 'reopened_from_observed_gaps'::text) IS NOT NULL);


--
-- Name: ix_locations_bounding_box; Type: INDEX; Schema: agri; Owner: -
--

CREATE INDEX ix_locations_bounding_box ON agri.locations USING gist (bounding_box);


--
-- Name: ix_locations_geometry; Type: INDEX; Schema: agri; Owner: -
--

CREATE INDEX ix_locations_geometry ON agri.locations USING gist (geometry);


--
-- Name: ix_soil_profiles_location_id; Type: INDEX; Schema: agri; Owner: -
--

CREATE INDEX ix_soil_profiles_location_id ON agri.soil_profiles USING btree (location_id);


--
-- Name: ix_spatial_cell_centroid; Type: INDEX; Schema: agri; Owner: -
--

CREATE INDEX ix_spatial_cell_centroid ON agri.spatial_cell USING gist (centroid);


--
-- Name: ix_spatial_cell_geometry; Type: INDEX; Schema: agri; Owner: -
--

CREATE INDEX ix_spatial_cell_geometry ON agri.spatial_cell USING gist (geometry);


--
-- Name: ix_spatial_cell_grid_resolution; Type: INDEX; Schema: agri; Owner: -
--

CREATE INDEX ix_spatial_cell_grid_resolution ON agri.spatial_cell USING btree (grid_name, resolution_m);


--
-- Name: ix_topography_profiles_location_id; Type: INDEX; Schema: agri; Owner: -
--

CREATE INDEX ix_topography_profiles_location_id ON agri.topography_profiles USING btree (location_id);


--
-- Name: ix_water_profiles_location_id; Type: INDEX; Schema: agri; Owner: -
--

CREATE INDEX ix_water_profiles_location_id ON agri.water_profiles USING btree (location_id);


--
-- Name: job_event_default_job_run_id_occurred_at_idx; Type: INDEX; Schema: agri; Owner: -
--

CREATE INDEX job_event_default_job_run_id_occurred_at_idx ON agri.job_event_default USING btree (job_run_id, occurred_at);


--
-- Name: job_event_default_severity_occurred_at_idx; Type: INDEX; Schema: agri; Owner: -
--

CREATE INDEX job_event_default_severity_occurred_at_idx ON agri.job_event_default USING btree (severity, occurred_at);


--
-- Name: job_event_default_job_run_id_occurred_at_idx; Type: INDEX ATTACH; Schema: agri; Owner: -
--

ALTER INDEX agri.ix_job_event_run_occurred ATTACH PARTITION agri.job_event_default_job_run_id_occurred_at_idx;


--
-- Name: job_event_default_pkey; Type: INDEX ATTACH; Schema: agri; Owner: -
--

ALTER INDEX agri.pk_job_event ATTACH PARTITION agri.job_event_default_pkey;


--
-- Name: job_event_default_severity_occurred_at_idx; Type: INDEX ATTACH; Schema: agri; Owner: -
--

ALTER INDEX agri.ix_job_event_severity_occurred ATTACH PARTITION agri.job_event_default_severity_occurred_at_idx;


--
-- Name: expert_label expert_label_review_guard; Type: TRIGGER; Schema: agri; Owner: -
--

CREATE TRIGGER expert_label_review_guard BEFORE INSERT OR DELETE OR UPDATE ON agri.expert_label FOR EACH ROW EXECUTE FUNCTION agri.guard_expert_label_review_change();


--
-- Name: climate_profiles fk_climate_profiles_location_id_locations; Type: FK CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE ONLY agri.climate_profiles
    ADD CONSTRAINT fk_climate_profiles_location_id_locations FOREIGN KEY (location_id) REFERENCES agri.locations(id);


--
-- Name: companion_relationships fk_companion_relationships_species_a_id_species; Type: FK CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE ONLY agri.companion_relationships
    ADD CONSTRAINT fk_companion_relationships_species_a_id_species FOREIGN KEY (species_a_id) REFERENCES agri.species(id);


--
-- Name: companion_relationships fk_companion_relationships_species_b_id_species; Type: FK CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE ONLY agri.companion_relationships
    ADD CONSTRAINT fk_companion_relationships_species_b_id_species FOREIGN KEY (species_b_id) REFERENCES agri.species(id);


--
-- Name: expert_label fk_expert_label_release; Type: FK CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE ONLY agri.expert_label
    ADD CONSTRAINT fk_expert_label_release FOREIGN KEY (release_id) REFERENCES agri.expert_label_release(id);


--
-- Name: expert_label fk_expert_label_source; Type: FK CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE ONLY agri.expert_label
    ADD CONSTRAINT fk_expert_label_source FOREIGN KEY (source_id) REFERENCES agri.expert_label_source(id);


--
-- Name: job_attempt fk_job_attempt_job_work_item_id_job_work_item; Type: FK CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE ONLY agri.job_attempt
    ADD CONSTRAINT fk_job_attempt_job_work_item_id_job_work_item FOREIGN KEY (job_work_item_id) REFERENCES agri.job_work_item(id) ON DELETE CASCADE;


--
-- Name: job_checkpoint fk_job_checkpoint_attempt_fence; Type: FK CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE ONLY agri.job_checkpoint
    ADD CONSTRAINT fk_job_checkpoint_attempt_fence FOREIGN KEY (job_attempt_id, job_work_item_id, fencing_token) REFERENCES agri.job_attempt(id, job_work_item_id, fencing_token);


--
-- Name: job_checkpoint fk_job_checkpoint_job_work_item_id_job_work_item; Type: FK CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE ONLY agri.job_checkpoint
    ADD CONSTRAINT fk_job_checkpoint_job_work_item_id_job_work_item FOREIGN KEY (job_work_item_id) REFERENCES agri.job_work_item(id) ON DELETE CASCADE;


--
-- Name: job_dependency fk_job_dependency_depends_on_run_id_job_run; Type: FK CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE ONLY agri.job_dependency
    ADD CONSTRAINT fk_job_dependency_depends_on_run_id_job_run FOREIGN KEY (depends_on_run_id) REFERENCES agri.job_run(id);


--
-- Name: job_dependency fk_job_dependency_job_run_id_job_run; Type: FK CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE ONLY agri.job_dependency
    ADD CONSTRAINT fk_job_dependency_job_run_id_job_run FOREIGN KEY (job_run_id) REFERENCES agri.job_run(id) ON DELETE CASCADE;


--
-- Name: job_event fk_job_event_job_attempt_id_job_attempt; Type: FK CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE agri.job_event
    ADD CONSTRAINT fk_job_event_job_attempt_id_job_attempt FOREIGN KEY (job_attempt_id) REFERENCES agri.job_attempt(id) ON DELETE CASCADE;


--
-- Name: job_event fk_job_event_job_run_id_job_run; Type: FK CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE agri.job_event
    ADD CONSTRAINT fk_job_event_job_run_id_job_run FOREIGN KEY (job_run_id) REFERENCES agri.job_run(id) ON DELETE CASCADE;


--
-- Name: job_event fk_job_event_job_work_item_id_job_work_item; Type: FK CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE agri.job_event
    ADD CONSTRAINT fk_job_event_job_work_item_id_job_work_item FOREIGN KEY (job_work_item_id) REFERENCES agri.job_work_item(id) ON DELETE CASCADE;


--
-- Name: job_incident fk_job_incident_job_run_id_job_run; Type: FK CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE ONLY agri.job_incident
    ADD CONSTRAINT fk_job_incident_job_run_id_job_run FOREIGN KEY (job_run_id) REFERENCES agri.job_run(id);


--
-- Name: job_incident fk_job_incident_job_work_item_id_job_work_item; Type: FK CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE ONLY agri.job_incident
    ADD CONSTRAINT fk_job_incident_job_work_item_id_job_work_item FOREIGN KEY (job_work_item_id) REFERENCES agri.job_work_item(id);


--
-- Name: job_output fk_job_output_attempt_fence; Type: FK CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE ONLY agri.job_output
    ADD CONSTRAINT fk_job_output_attempt_fence FOREIGN KEY (job_attempt_id, job_work_item_id, fencing_token) REFERENCES agri.job_attempt(id, job_work_item_id, fencing_token);


--
-- Name: job_output fk_job_output_job_run_id_job_run; Type: FK CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE ONLY agri.job_output
    ADD CONSTRAINT fk_job_output_job_run_id_job_run FOREIGN KEY (job_run_id) REFERENCES agri.job_run(id) ON DELETE CASCADE;


--
-- Name: job_output fk_job_output_work_item_run; Type: FK CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE ONLY agri.job_output
    ADD CONSTRAINT fk_job_output_work_item_run FOREIGN KEY (job_work_item_id, job_run_id) REFERENCES agri.job_work_item(id, job_run_id);


--
-- Name: job_run fk_job_run_job_definition_id_job_definition; Type: FK CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE ONLY agri.job_run
    ADD CONSTRAINT fk_job_run_job_definition_id_job_definition FOREIGN KEY (job_definition_id) REFERENCES agri.job_definition(id);


--
-- Name: job_work_item fk_job_work_item_job_run_id_job_run; Type: FK CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE ONLY agri.job_work_item
    ADD CONSTRAINT fk_job_work_item_job_run_id_job_run FOREIGN KEY (job_run_id) REFERENCES agri.job_run(id) ON DELETE CASCADE;


--
-- Name: soil_profiles fk_soil_profiles_location_id_locations; Type: FK CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE ONLY agri.soil_profiles
    ADD CONSTRAINT fk_soil_profiles_location_id_locations FOREIGN KEY (location_id) REFERENCES agri.locations(id);


--
-- Name: spatial_cell fk_spatial_cell_parent_cell_id_spatial_cell; Type: FK CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE ONLY agri.spatial_cell
    ADD CONSTRAINT fk_spatial_cell_parent_cell_id_spatial_cell FOREIGN KEY (parent_cell_id) REFERENCES agri.spatial_cell(id);


--
-- Name: topography_profiles fk_topography_profiles_location_id_locations; Type: FK CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE ONLY agri.topography_profiles
    ADD CONSTRAINT fk_topography_profiles_location_id_locations FOREIGN KEY (location_id) REFERENCES agri.locations(id);


--
-- Name: water_profiles fk_water_profiles_location_id_locations; Type: FK CONSTRAINT; Schema: agri; Owner: -
--

ALTER TABLE ONLY agri.water_profiles
    ADD CONSTRAINT fk_water_profiles_location_id_locations FOREIGN KEY (location_id) REFERENCES agri.locations(id);


--
-- PostgreSQL database dump complete
--


