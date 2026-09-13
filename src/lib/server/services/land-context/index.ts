/**
 * Public surface of the land-context reader service. See `./reader.ts` for
 * the bounded read functions, `./budgets.ts` for frozen numeric limits,
 * `./inquiry-draft.ts` for the draft-only inquiry assembler and `./types.ts`
 * for the shared result/coverage types this whole module returns.
 */

export * from "./types";
export * from "./budgets";
export {
  readPointContainment,
  readBoundedAoiIntersection,
  readBoundaryByParcelKey,
  readContactsForSubject,
  readCoverageForRegion,
} from "./reader";
export { draftInquiry } from "./inquiry-draft";
export type { DraftInquiryInput, DraftInquiryResult } from "./inquiry-draft";
export type { BboxDegrees } from "./parquet-reader";
