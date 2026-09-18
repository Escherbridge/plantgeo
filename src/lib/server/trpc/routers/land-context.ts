import { z } from "zod";
import { router, publicProcedure } from "@/lib/server/trpc/init";
import {
  attachDecodedGeometries,
  attachDecodedGeometryToOne,
  draftInquiry,
  readBoundedAoiIntersection,
  readBoundaryByParcelKey,
  readContactsForSubject,
  readCoverageForRegion,
  readPointContainment,
  MAX_FEATURES_RETURNED,
  PILOT_STATES,
} from "@/lib/server/services/land-context";

/**
 * Read-only tRPC surface for the PNW land-context reference plane.
 *
 * Implements the reference-plane spec's "Bounded readers and agent contract"
 * plus the contact-experience spec's "Agent parity" section: the agent must
 * use the same selected point/area, topic, source versions and availability
 * contract as the map/detail panel, and every result must carry the same
 * source/overlap/organization/route/evidence/gaps shape either surface uses.
 *
 * All procedures are public and read-only. `draftInquiry` assembles text
 * only — it has no side effects and sends nothing.
 */

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

export const landContextRouter = router({
  /**
   * Boundary resolution by point containment. Returns every containing
   * feature (never just the nearest), each with its overlap basis and
   * source reference. Each result also carries `geometry`, the boundary's
   * WKB decoded server-side (see `attachDecodedGeometry`): browser code may
   * not import the decoder, and the frozen contract carries only hex WKB.
   */
  resolveBoundaryAtPoint: publicProcedure
    .input(
      z.object({
        lon: z.number().min(-180).max(180),
        lat: z.number().min(-90).max(90),
        maxFeatures: z.number().int().min(1).max(MAX_FEATURES_RETURNED).optional(),
      })
    )
    .query(async ({ input }) => {
      return attachDecodedGeometries(
        await readPointContainment(input.lon, input.lat, { maxFeatures: input.maxFeatures })
      );
    }),

  /**
   * Boundary resolution over a bounded AOI (bbox). Bbox pruning happens
   * before exact intersection in the underlying reader; the AOI area is
   * capped at `MAX_AOI_AREA_SQUARE_DEGREES` and an over-budget request
   * returns a typed `budget_exceeded` response, never a silent truncation.
   */
  resolveBoundaryInArea: publicProcedure
    .input(
      z.object({
        bbox: bboxSchema,
        maxFeatures: z.number().int().min(1).max(MAX_FEATURES_RETURNED).optional(),
      })
    )
    .query(async ({ input }) => {
      return attachDecodedGeometries(
        await readBoundedAoiIntersection(input.bbox, { maxFeatures: input.maxFeatures })
      );
    }),

  /** Boundary resolution by a validated parcel key (county/source namespace + original ID). */
  resolveBoundaryByParcelKey: publicProcedure
    .input(parcelKeySchema)
    .query(async ({ input }) => {
      return attachDecodedGeometryToOne(await readBoundaryByParcelKey(input));
    }),

  /**
   * Responsible/public-inquiry contact lookup and topic/geography adviser
   * lookup by validated subject ID and optional topic. Returns every
   * applicable office/route with its route meaning, assignment evidence and
   * review status — never a single "best guess" contact.
   */
  lookupContactsForSubject: publicProcedure
    .input(
      z.object({
        subjectId: z.string().trim().min(1).max(200),
        topic: z.string().trim().min(1).max(200).nullable().default(null),
        maxFeatures: z.number().int().min(1).max(MAX_FEATURES_RETURNED).optional(),
      })
    )
    .query(async ({ input }) => {
      return readContactsForSubject(input.subjectId, input.topic, {
        maxFeatures: input.maxFeatures,
      });
    }),

  /** Coverage status for a state/county, independent of any specific feature lookup. */
  coverageStatus: publicProcedure
    .input(
      z.object({
        state: pilotStateSchema,
        county: z.string().trim().min(1).max(200).nullable().default(null),
      })
    )
    .query(async ({ input }) => {
      return readCoverageForRegion(input.state, input.county);
    }),

  /**
   * Assembles an editable inquiry draft. This procedure has NO side
   * effects: it returns a string for the caller to review and copy. It
   * never sends an email, submits a form, or contacts anyone, and it never
   * claims introduction/forwarding capability beyond what the resolved
   * contact route documents.
   */
  draftInquiry: publicProcedure
    .input(
      z.object({
        parcelKey: parcelKeySchema.nullable().default(null),
        county: z.string().trim().min(1).max(200).nullable().default(null),
        state: pilotStateSchema,
        userProvidedIdea: z.string().trim().max(4_000),
        // The resolved contact result the caller already fetched via
        // `lookupContactsForSubject` — this procedure does not re-resolve
        // it, so UI and agent stay pinned to the same evidence.
        contact: z.object({
          coverageState: z.enum([
            "matched",
            "no_match_in_proven_coverage",
            "unknown_coverage",
            "unavailable_history",
            "outside_pilot",
            "partial_area_coverage",
          ]),
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
        }),
      })
    )
    .query(({ input }) => {
      return draftInquiry({
        parcelKey: input.parcelKey,
        county: input.county,
        state: input.state,
        userProvidedIdea: input.userProvidedIdea,
        // Cast: the input schema intentionally validates only the fields
        // `draftInquiry` reads, not the full `LandContextResult` shape, so
        // callers don't have to round-trip every internal field through
        // the wire. Safe because `draftInquiry` only touches the fields
        // validated above.
        contact: input.contact as Parameters<typeof draftInquiry>[0]["contact"],
      });
    }),
});
