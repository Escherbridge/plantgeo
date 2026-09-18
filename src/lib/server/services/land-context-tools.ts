import { z } from "zod";
import {
  draftInquiry,
  readBoundedAoiIntersection,
  readBoundaryByParcelKey,
  readContactsForSubject,
  readCoverageForRegion,
  readPointContainment,
  COVERAGE_STATES,
  MAX_FEATURES_RETURNED,
  PILOT_STATES,
  type LandContextResult,
} from "@/lib/server/services/land-context";

/**
 * AI-assistant tool registration for the PNW land-context reference plane.
 *
 * Paired with `regional-evidence-tools.ts`, whose `loadRegionalEvidenceTools`/
 * `callRegionalEvidenceTool` merge this module's tools into the catalogue
 * `ai-prompt.ts` already consumes. Unlike the agri Parquet lanes (dispatched
 * over the external `AGRI_PARQUET_SERVICE_URL` HTTP bridge), land-context
 * lives in this Next.js process's own Postgres/Drizzle-backed reader
 * service, so these tools call `@/lib/server/services/land-context` directly
 * — no network hop, no separate provider contract.
 *
 * `src/lib/server/trpc/routers/land-context.ts` and
 * `src/lib/server/services/land-context/**` are frozen for this change: this
 * file only consumes their public exports, matching the same six procedures
 * (`resolveBoundaryAtPoint`, `resolveBoundaryInArea`, `resolveBoundaryByParcelKey`,
 * `lookupContactsForSubject`, `coverageStatus`, `draftInquiry`) with the same
 * validation the tRPC router applies, so the agent surface and the map/panel
 * UI stay pinned to identical readers and identical evidence.
 */

const SCOPE_NOTE =
  "Pilot scope: Washington, Oregon and Idaho (WA/OR/ID) only. Results never include private owner names or personal contact fields — only public office/organization contacts and documented public routes appear.";

const pilotStateSchema = z.enum(PILOT_STATES);

const bboxSchema = z
  .object({
    west: z.number().min(-180).max(180),
    south: z.number().min(-90).max(90),
    east: z.number().min(-180).max(180),
    north: z.number().min(-90).max(90),
  })
  .refine(({ west, south, east, north }) => west < east && south < north, {
    message: "bbox must be ordered west,south,east,north",
  });

const parcelKeySchema = z.object({
  sourceNamespace: z.string().trim().min(1).max(200),
  originalId: z.string().trim().min(1).max(200),
  state: pilotStateSchema,
});

/**
 * The wire-shaped mirror of `LandContextResult`'s nested `contact` fields
 * that `draftInquiry` reads, matching the tRPC router's own cast comment:
 * the tool input only validates the fields `draftInquiry` touches, not the
 * full reader result shape.
 */
const contactInputSchema = z.object({
  coverageState: z.enum(COVERAGE_STATES),
  organizationOffice: z
    .object({
      organizationId: z.string(),
      officeId: z.string(),
      officialPublicName: z.string(),
      officeType: z.string(),
      parentOrganizationId: z.string().nullable(),
    })
    .nullable(),
  route: z
    .object({
      officeId: z.string(),
      routeType: z.string(),
      routeMeaning: z.enum([
        "records_assistance",
        "responsible_agency_program",
        "advisory_sme",
        "contact_process_inquiry",
        "documented_introduction_forwarding",
      ]),
      documentedTopic: z.string().nullable(),
      documentedHelp: z.string().nullable(),
      officialInquiryUrl: z.string().nullable(),
      publicPhone: z.string().nullable(),
      publicEmail: z.string().nullable(),
      optionalProfessionalName: z.string().nullable(),
      status: z.enum(["active", "stale", "broken", "unverified"]),
      verificationTime: z.string().nullable(),
      supportsIntroductionOrForwarding: z.boolean(),
    })
    .nullable(),
  roleOrRouteType: z.string().nullable(),
  publicContactUrl: z.string().nullable(),
  documentedHelp: z.string().nullable(),
  unresolvedGaps: z.array(z.string()),
});

interface LandContextTool {
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
  /** True for every tool here — the land-context surface is entirely reads. */
  readOnly: true;
  handler: (args: Record<string, unknown>) => Promise<unknown> | unknown;
}

function maxFeaturesProperty() {
  return {
    type: "integer",
    minimum: 1,
    maximum: MAX_FEATURES_RETURNED,
    description: `Optional cap on returned features, up to ${MAX_FEATURES_RETURNED} (the same bound the map/panel UI uses).`,
  };
}

const RESOLVE_BOUNDARY_AT_POINT_TOOL: LandContextTool = {
  name: "resolve_land_boundary_at_point",
  description:
    `Resolve every land/administrative boundary containing a WA/OR/ID point (parcel, jurisdiction, or other tracked boundary family) using the same reader as the map's boundary detail panel. Returns ALL containing features with their overlap basis and source release, not just the nearest one. ${SCOPE_NOTE} Read-only.`,
  input_schema: {
    type: "object",
    additionalProperties: false,
    properties: {
      lon: { type: "number", minimum: -180, maximum: 180, description: "Longitude, WGS84." },
      lat: { type: "number", minimum: -90, maximum: 90, description: "Latitude, WGS84." },
      maxFeatures: maxFeaturesProperty(),
    },
    required: ["lon", "lat"],
  },
  readOnly: true,
  handler: (args) => {
    const input = z
      .object({
        lon: z.number().min(-180).max(180),
        lat: z.number().min(-90).max(90),
        maxFeatures: z.number().int().min(1).max(MAX_FEATURES_RETURNED).optional(),
      })
      .parse(args);
    return readPointContainment(input.lon, input.lat, { maxFeatures: input.maxFeatures });
  },
};

const RESOLVE_BOUNDARY_IN_AREA_TOOL: LandContextTool = {
  name: "resolve_land_boundary_in_area",
  description:
    `Resolve land/administrative boundaries intersecting a bounded WA/OR/ID area of interest (bbox), using the same reader and area cap as the map/panel UI. An over-budget area returns a typed budget_exceeded response rather than a silent truncation. ${SCOPE_NOTE} Read-only.`,
  input_schema: {
    type: "object",
    additionalProperties: false,
    properties: {
      bbox: {
        type: "object",
        additionalProperties: false,
        properties: {
          west: { type: "number", minimum: -180, maximum: 180 },
          south: { type: "number", minimum: -90, maximum: 90 },
          east: { type: "number", minimum: -180, maximum: 180 },
          north: { type: "number", minimum: -90, maximum: 90 },
        },
        required: ["west", "south", "east", "north"],
        description: "Bounding box in WGS84 degrees, ordered west < east and south < north.",
      },
      maxFeatures: maxFeaturesProperty(),
    },
    required: ["bbox"],
  },
  readOnly: true,
  handler: (args) => {
    const input = z
      .object({ bbox: bboxSchema, maxFeatures: z.number().int().min(1).max(MAX_FEATURES_RETURNED).optional() })
      .parse(args);
    return readBoundedAoiIntersection(input.bbox, { maxFeatures: input.maxFeatures });
  },
};

const RESOLVE_BOUNDARY_BY_PARCEL_KEY_TOOL: LandContextTool = {
  name: "resolve_land_boundary_by_parcel_key",
  description:
    `Resolve a land boundary by its validated parcel/tract identity (county/source namespace plus original upstream ID, leading zeros preserved) in WA/OR/ID. Use this when the user has cited a specific parcel rather than a coordinate. ${SCOPE_NOTE} Read-only.`,
  input_schema: {
    type: "object",
    additionalProperties: false,
    properties: {
      sourceNamespace: { type: "string", minLength: 1, maxLength: 200, description: "County/source namespace, e.g. \"wa-king-county-assessor\"." },
      originalId: { type: "string", minLength: 1, maxLength: 200, description: "Original upstream string ID, leading zeros intact." },
      state: { type: "string", enum: PILOT_STATES },
    },
    required: ["sourceNamespace", "originalId", "state"],
  },
  readOnly: true,
  handler: (args) => readBoundaryByParcelKey(parcelKeySchema.parse(args)),
};

const LOOKUP_CONTACTS_FOR_SUBJECT_TOOL: LandContextTool = {
  name: "lookup_land_contacts_for_subject",
  description:
    `Look up every applicable public office, program adviser, or documented contact route for a resolved land-context subject (parcel or boundary feature ID), optionally scoped by topic. Returns every applicable office/route with its route meaning, assignment evidence and review status — never a single "best guess" contact. ${SCOPE_NOTE} Read-only.`,
  input_schema: {
    type: "object",
    additionalProperties: false,
    properties: {
      subjectId: { type: "string", minLength: 1, maxLength: 200, description: "The subject ID from a prior boundary resolution result." },
      topic: { type: "string", minLength: 1, maxLength: 200, nullable: true, description: "Optional topic to scope contact routes to (e.g. a land-use or program topic)." },
      maxFeatures: maxFeaturesProperty(),
    },
    required: ["subjectId"],
  },
  readOnly: true,
  handler: (args) => {
    const input = z
      .object({
        subjectId: z.string().trim().min(1).max(200),
        topic: z.string().trim().min(1).max(200).nullable().default(null),
        maxFeatures: z.number().int().min(1).max(MAX_FEATURES_RETURNED).optional(),
      })
      .parse(args);
    return readContactsForSubject(input.subjectId, input.topic, { maxFeatures: input.maxFeatures });
  },
};

const COVERAGE_STATUS_TOOL: LandContextTool = {
  name: "land_context_coverage_status",
  description:
    `Report land-context coverage status for a WA/OR/ID state and optional county, independent of any specific feature lookup — use this to tell the user whether a region is covered before or instead of a point/area lookup. ${SCOPE_NOTE} Read-only.`,
  input_schema: {
    type: "object",
    additionalProperties: false,
    properties: {
      state: { type: "string", enum: PILOT_STATES },
      county: { type: "string", minLength: 1, maxLength: 200, nullable: true },
    },
    required: ["state"],
  },
  readOnly: true,
  handler: (args) => {
    const input = z
      .object({ state: pilotStateSchema, county: z.string().trim().min(1).max(200).nullable().default(null) })
      .parse(args);
    return readCoverageForRegion(input.state, input.county);
  },
};

const DRAFT_LAND_INQUIRY_TEXT_TOOL: LandContextTool = {
  name: "draft_land_inquiry_text",
  description:
    "Assemble EDITABLE DRAFT TEXT for a land-context inquiry, for the user to review and send themselves. " +
    "This tool NEVER sends an email, submits a form, or contacts anyone — it has no side effects and performs " +
    "no outbound communication of any kind; it only returns text. Always present the result to the user as a " +
    "draft they must review and send on their own, and never say or imply that an inquiry has already been sent, " +
    "submitted, or forwarded on their behalf. Requires a contact result already obtained from " +
    `lookup_land_contacts_for_subject in the same conversation, so the draft stays pinned to the same evidence shown in the UI. ${SCOPE_NOTE}`,
  input_schema: {
    type: "object",
    additionalProperties: false,
    properties: {
      parcelKey: {
        type: "object",
        nullable: true,
        additionalProperties: false,
        properties: {
          sourceNamespace: { type: "string", minLength: 1, maxLength: 200 },
          originalId: { type: "string", minLength: 1, maxLength: 200 },
          state: { type: "string", enum: PILOT_STATES },
        },
        required: ["sourceNamespace", "originalId", "state"],
      },
      county: { type: "string", minLength: 1, maxLength: 200, nullable: true },
      state: { type: "string", enum: PILOT_STATES },
      userProvidedIdea: {
        type: "string",
        maxLength: 4000,
        description: "The user's own stated question or idea for the inquiry — never fabricated on their behalf.",
      },
      contact: {
        type: "object",
        description:
          "The exact contact result object previously returned by lookup_land_contacts_for_subject for this subject — this tool does not re-resolve contacts, so the draft and the displayed evidence stay pinned to the same lookup.",
      },
    },
    required: ["state", "userProvidedIdea", "contact"],
  },
  readOnly: true,
  handler: (args) => {
    const input = z
      .object({
        parcelKey: parcelKeySchema.nullable().default(null),
        county: z.string().trim().min(1).max(200).nullable().default(null),
        state: pilotStateSchema,
        userProvidedIdea: z.string().trim().max(4_000),
        contact: contactInputSchema,
      })
      .parse(args);
    return draftInquiry({
      parcelKey: input.parcelKey,
      county: input.county,
      state: input.state,
      userProvidedIdea: input.userProvidedIdea,
      // Same cast rationale as the tRPC router: the tool input only validates
      // the fields `draftInquiry` reads, not the full `LandContextResult`.
      contact: input.contact as LandContextResult,
    });
  },
};

export const LAND_CONTEXT_TOOLS: LandContextTool[] = [
  RESOLVE_BOUNDARY_AT_POINT_TOOL,
  RESOLVE_BOUNDARY_IN_AREA_TOOL,
  RESOLVE_BOUNDARY_BY_PARCEL_KEY_TOOL,
  LOOKUP_CONTACTS_FOR_SUBJECT_TOOL,
  COVERAGE_STATUS_TOOL,
  DRAFT_LAND_INQUIRY_TEXT_TOOL,
];

const LAND_CONTEXT_TOOL_NAMES = new Set(LAND_CONTEXT_TOOLS.map((tool) => tool.name));

export function isLandContextTool(name: string): boolean {
  return LAND_CONTEXT_TOOL_NAMES.has(name);
}

/**
 * Runs a land-context tool in-process (no network hop) and returns its
 * result JSON-stringified, matching `callRegionalEvidenceTool`'s return
 * shape so callers treat both sources identically.
 */
export async function callLandContextTool(name: string, args: Record<string, unknown>): Promise<string> {
  const tool = LAND_CONTEXT_TOOLS.find((candidate) => candidate.name === name);
  if (!tool) throw new Error(`Unknown land-context tool: ${name}`);
  const result = await tool.handler(args);
  return JSON.stringify(result);
}
