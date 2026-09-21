import { z } from "zod";
import { readContactsForSelection } from "@/lib/server/services/land-context/reader";
import { readCropCover } from "@/lib/server/services/land-context/crop-cover";
import { daySchema } from "@/lib/server/services/parquet-trpc-readers/shared";
import {
  draftInquiry,
  readBoundedAoiIntersection,
  readBoundaryByParcelKey,
  readContactsForSubject,
  readCoverageForRegion,
  readPointContainment,
  COVERAGE_STATES,
  MAX_FEATURES_RETURNED,
  admittedSubdivisionCodeSchema,
  admittedSubdivisionCodes,
  isLandContextBoundInRegion,
  landContextUnboundDetail,
  regionDisplayName,
  regionSlug,
  type LandContextResult,
} from "@/lib/server/services/land-context";

/**
 * AI-assistant tool registration for the land-context reference plane.
 *
 * Paired with `regional-evidence-tools.ts`, whose `loadRegionalEvidenceTools`/
 * `callRegionalEvidenceTool` merge this module's tools into the catalogue
 * `ai-prompt.ts` already consumes. Unlike the agri Parquet lanes (dispatched
 * over the external `AGRI_PARQUET_SERVICE_URL` HTTP bridge), land-context
 * lives in this Next.js process's own Postgres/Drizzle-backed reader
 * service, so these tools call `@/lib/server/services/land-context` directly
 * — no network hop, no separate provider contract.
 *
 * This file only consumes `src/lib/server/trpc/routers/land-context.ts` and
 * `src/lib/server/services/land-context/**` through their public exports,
 * matching the same six procedures
 * (`resolveBoundaryAtPoint`, `resolveBoundaryInArea`, `resolveBoundaryByParcelKey`,
 * `lookupContactsForSubject`, `coverageStatus`, `draftInquiry`) with the same
 * validation the tRPC router applies, so the agent surface and the map/panel
 * UI stay pinned to identical readers and identical evidence -- the region gate
 * included: `callLandContextTool` refuses in a region that binds no land-context
 * source, and every description and `state` enum below is built from the SELECTED
 * manifest per catalogue load (see `land-context/AGENTS.md` §region-binding).
 */

/**
 * The scope sentence every tool description carries, spoken in the SELECTED region's own terms.
 *
 * Was a frozen "Pilot scope: Washington, Oregon and Idaho (WA/OR/ID) only" that a Kenya deployment
 * would have read out verbatim (STYLE-REVIEW-W8 B1). Built per catalogue load, so the names and
 * codes a model is told about are always this deployment's.
 */
function scopeNote(): string {
  const codes = admittedSubdivisionCodes();
  return (
    `Scope: ${regionDisplayName()} only (${codes.join("/")}). Results never include private owner ` +
    `names or personal contact fields — only public office/organization contacts and documented ` +
    `public routes appear.`
  );
}

/** "Pacific Northwest (WA/OR/ID)": how a tool description names the ground it covers, per call. */
function admittedAreaPhrase(): string {
  return `${regionDisplayName()} (${admittedSubdivisionCodes().join("/")})`;
}

/** The admitted subdivision codes as a JSON-schema enum; read per catalogue load, never frozen. */
function admittedStateProperty(): Record<string, unknown> {
  return { type: "string", enum: [...admittedSubdivisionCodes()] };
}

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
  state: admittedSubdivisionCodeSchema,
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

function resolveBoundaryAtPointTool(): LandContextTool {
  return {
    name: "resolve_land_boundary_at_point",
    description:
      `Resolve every land/administrative boundary containing a point in ${admittedAreaPhrase()} (parcel, jurisdiction, or other tracked boundary family) using the same reader as the map's boundary detail panel. Returns ALL containing features with their overlap basis and source release, not just the nearest one. ${scopeNote()} Read-only.`,
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
}

function resolveBoundaryInAreaTool(): LandContextTool {
  return {
    name: "resolve_land_boundary_in_area",
    description:
      `Resolve land/administrative boundaries intersecting a bounded area of interest in ${admittedAreaPhrase()} (bbox), using the same reader and area cap as the map/panel UI. An over-budget area returns a typed budget_exceeded response rather than a silent truncation. ${scopeNote()} Read-only.`,
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
}

function resolveBoundaryByParcelKeyTool(): LandContextTool {
  return {
    name: "resolve_land_boundary_by_parcel_key",
    description:
      `Resolve a land boundary by its validated parcel/tract identity (county/source namespace plus original upstream ID, leading zeros preserved) in ${admittedAreaPhrase()}. Use this when the user has cited a specific parcel rather than a coordinate. ${scopeNote()} Read-only.`,
    input_schema: {
      type: "object",
      additionalProperties: false,
      properties: {
        sourceNamespace: { type: "string", minLength: 1, maxLength: 200, description: "County/source namespace, e.g. \"wa-king-county-assessor\"." },
        originalId: { type: "string", minLength: 1, maxLength: 200, description: "Original upstream string ID, leading zeros intact." },
        state: admittedStateProperty(),
      },
      required: ["sourceNamespace", "originalId", "state"],
    },
    readOnly: true,
    handler: (args) => readBoundaryByParcelKey(parcelKeySchema.parse(args)),
  };
}

function lookupContactsForSubjectTool(): LandContextTool {
  return {
    name: "lookup_land_contacts_for_subject",
    description:
      `Look up every applicable public office, program adviser, or documented contact route for an explicit office subject ID (not a surface management feature ID), optionally scoped by topic. Returns every applicable office/route with its route meaning, assignment evidence and review status — never a single "best guess" contact. ${scopeNote()} Read-only.`,
    input_schema: {
      type: "object",
      additionalProperties: false,
      properties: {
        subjectId: { type: "string", minLength: 1, maxLength: 200, description: "The office subject ID from a prior spatial office lookup." },
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
}

function coverageStatusTool(): LandContextTool {
  return {
    name: "land_context_coverage_status",
    description:
      `Report land-context coverage status for a subdivision of ${admittedAreaPhrase()} and an optional county, independent of any specific feature lookup — use this to tell the user whether a region is covered before or instead of a point/area lookup. ${scopeNote()} Read-only.`,
    input_schema: {
      type: "object",
      additionalProperties: false,
      properties: {
        state: admittedStateProperty(),
        county: { type: "string", minLength: 1, maxLength: 200, nullable: true },
      },
      required: ["state"],
    },
    readOnly: true,
    handler: (args) => {
      const input = z
        .object({ state: admittedSubdivisionCodeSchema, county: z.string().trim().min(1).max(200).nullable().default(null) })
        .parse(args);
      return readCoverageForRegion(input.state, input.county);
    },
  };
}

function contactsAtPointTool(): LandContextTool {
  return {
    name: "lookup_land_contacts_at_point",
    description: `Find public office jurisdictions containing the selected point, then their documented contact routes. Geographic overlap establishes neither legal authority nor responsibility for a particular program. ${scopeNote()} Read-only.`,
    readOnly: true,
    input_schema: { type: "object", additionalProperties: false, properties: {
      lon: { type: "number", minimum: -180, maximum: 180 },
      lat: { type: "number", minimum: -90, maximum: 90 },
    }, required: ["lon", "lat"] },
    handler: (args) => {
      const input = z.object({ lon: z.number().min(-180).max(180), lat: z.number().min(-90).max(90) }).parse(args);
      return readContactsForSelection({ mode: "point", ...input });
    },
  };
}

function contactsInAreaTool(): LandContextTool {
  return {
    name: "lookup_land_contacts_in_area",
    description: `Find published public office jurisdictions intersecting a selected area and return documented routes. Do not infer program duties or permission from geographic overlap. ${scopeNote()} Read-only.`,
    readOnly: true,
    input_schema: { type: "object", additionalProperties: false, properties: {
      bbox: { type: "object", properties: {
        west: { type: "number" }, south: { type: "number" }, east: { type: "number" }, north: { type: "number" },
      }, required: ["west", "south", "east", "north"], additionalProperties: false },
    }, required: ["bbox"] },
    handler: (args) => readContactsForSelection({ mode: "area", ...z.object({ bbox: bboxSchema }).parse(args) }),
  };
}

function cropCoverInAreaTool(): LandContextTool {
  return {
    name: "read_crop_cover_in_area",
    description: `Read the USDA crop-cover edition published on or before the selected release day for a bounded view. Return the observed year, publication date, classified pixel area shares, resolutions and source provenance. Estimated grid cells are not parcels; crop fractions are not confidence scores. ${scopeNote()} Read-only.`,
    readOnly: true,
    input_schema: { type: "object", additionalProperties: false, properties: {
      bbox: { type: "object", properties: {
        west: { type: "number" }, south: { type: "number" }, east: { type: "number" }, north: { type: "number" },
      }, required: ["west", "south", "east", "north"], additionalProperties: false },
      asOfDay: { type: "string", pattern: "^\\d{4}-\\d{2}-\\d{2}$", description: "Selected publication day, YYYY-MM-DD." },
      zoomTier: { type: "integer", enum: [0, 5, 9, 13] },
    }, required: ["bbox", "asOfDay", "zoomTier"] },
    handler: (args) => {
      const input = z.object({ bbox: bboxSchema, asOfDay: daySchema,
        zoomTier: z.union([z.literal(0), z.literal(5), z.literal(9), z.literal(13)]) }).parse(args);
      return readCropCover(input.bbox, input.asOfDay, input.zoomTier);
    },
  };
}

function draftLandInquiryTextTool(): LandContextTool {
  return {
    name: "draft_land_inquiry_text",
    description:
      "Assemble EDITABLE DRAFT TEXT for a land-context inquiry, for the user to review and send themselves. " +
      "This tool NEVER sends an email, submits a form, or contacts anyone — it has no side effects and performs " +
      "no outbound communication of any kind; it only returns text. Always present the result to the user as a " +
      "draft they must review and send on their own, and never say or imply that an inquiry has already been sent, " +
      "submitted, or forwarded on their behalf. Requires a contact result already obtained from " +
      `lookup_land_contacts_for_subject in the same conversation, so the draft stays pinned to the same evidence shown in the UI. ${scopeNote()}`,
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
            state: admittedStateProperty(),
          },
          required: ["sourceNamespace", "originalId", "state"],
        },
        county: { type: "string", minLength: 1, maxLength: 200, nullable: true },
        state: admittedStateProperty(),
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
          state: admittedSubdivisionCodeSchema,
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
}

/** The six builders, in catalogue order; functions, so no description is frozen at import. */
const LAND_CONTEXT_TOOL_BUILDERS = [
  resolveBoundaryAtPointTool,
  resolveBoundaryInAreaTool,
  resolveBoundaryByParcelKeyTool,
  lookupContactsForSubjectTool,
  coverageStatusTool,
  draftLandInquiryTextTool,
  contactsAtPointTool,
  contactsInAreaTool,
  cropCoverInAreaTool,
] as const;

/**
 * The land-context tools as this deployment's region describes them, built per catalogue load.
 *
 * Was a module-level array whose descriptions and `state` enums were frozen at import from the
 * pilot's literals, so a deployment covering anywhere else advertised
 * `resolve_land_boundary_by_parcel_key(state: "WA")` and prose reading "… in WA/OR/ID"
 * (STYLE-REVIEW-W8 B1). The six stay REGISTERED in every region -- a vocabulary that changed per
 * region would make the agent answer "I do not know that surface" for a layer the platform does
 * have -- and a region that binds no land-context source refuses them in `callLandContextTool`,
 * the same shape as the Python tree's `_region_absence` (`agent/tools.py`).
 */
export function landContextTools(): LandContextTool[] {
  return LAND_CONTEXT_TOOL_BUILDERS.map((build) => build());
}

/**
 * The six tool names, memoised on first use.
 *
 * Built lazily rather than at import because building a tool reads the region for its description,
 * and a module-scope manifest read is the import-time dependency `federation.md` §1 forbids. The
 * NAMES themselves are region-independent -- the vocabulary never changes with the manifest -- so
 * one cache serves every region this process ever resolves.
 */
let landContextToolNames: ReadonlySet<string> | null = null;

export function isLandContextTool(name: string): boolean {
  landContextToolNames ??= new Set(landContextTools().map((tool) => tool.name));
  return landContextToolNames.has(name);
}

/**
 * The governed absence every land-context tool answers where the region binds no source for it.
 *
 * Mirrors `agent/tools.py::_region_absence` field for field, `note` included: a REFUSAL and not an
 * absence, naming the layer and the region, so a model relaying it cannot restate it as "nothing
 * was found there" -- which is a claim about the ground rather than about the deployment
 * (`federation.md` §2).
 */
function landContextRegionAbsence(): Record<string, unknown> {
  return {
    error: "not_available_in_region",
    unbound_layers: ["land-context"],
    region_slug: regionSlug(),
    region_display_name: regionDisplayName(),
    note: landContextUnboundDetail(),
  };
}

/**
 * Runs a land-context tool in-process (no network hop) and returns its
 * result JSON-stringified, matching `callRegionalEvidenceTool`'s return
 * shape so callers treat both sources identically.
 */
export async function callLandContextTool(name: string, args: Record<string, unknown>): Promise<string> {
  const tool = landContextTools().find((candidate) => candidate.name === name);
  if (!tool) throw new Error(`Unknown land-context tool: ${name}`);
  // The region is asked BEFORE the arguments are parsed: in a region that binds no land-context
  // source there is no argument that would answer, and a validation error would blame the caller
  // for the deployment's footprint.
  if (name !== "read_crop_cover_in_area" && !isLandContextBoundInRegion()) return JSON.stringify(landContextRegionAbsence());
  const result = await tool.handler(args);
  return JSON.stringify(result);
}
