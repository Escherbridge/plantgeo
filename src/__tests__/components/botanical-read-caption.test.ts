/**
 * `describeBotanicalOccurrencesState` must tell a governed refusal from a failed read.
 *
 * Style review W8, S4: when the detail band collapsed onto the proxy lane, the plane's 400 and 503
 * refusals stopped arriving as a landed answer carrying a service-authored `note` and started
 * arriving as `phase: "error"` -- the same phase a dead socket produces. The caption printed
 * "Specimen records could not be loaded (<reason>)" for both and dropped `detail`, which is where
 * the plane's own explanation now lives. These cases pin the restored distinction; the first two
 * fail under the pre-fix wording.
 */
import { describe, expect, it } from "vitest";
import { describeBotanicalOccurrencesState } from "@/components/map/layers/BotanicalOccurrencesLayer";

const SETTLED = { isStale: false, isPartial: false, band: "detail", servingBand: "detail" } as const;

describe("describeBotanicalOccurrencesState: governed refusals keep the plane's words", () => {
  it("quotes the plane's own explanation of a 400 refusal", () => {
    const caption = describeBotanicalOccurrencesState({
      ...SETTLED,
      phase: "error",
      error: {
        error: "The botanical-occurrences plane refused this query",
        reason: "filter_not_served",
        detail: "this generation publishes no records for collection ubc:herbarium",
        kind: "governed_refusal",
      },
    });

    expect(caption).toBe(
      "The botanical-occurrences plane refused this query: this generation publishes no records for collection ubc:herbarium"
    );
    expect(caption).not.toContain("could not be loaded");
  });

  it("quotes the plane's own explanation of a 503 governed absence", () => {
    const caption = describeBotanicalOccurrencesState({
      ...SETTLED,
      phase: "error",
      error: {
        error: "The botanical-occurrences plane is unavailable",
        reason: "pointer_checksum_mismatch",
        detail: "the bytes under the current pointer do not match its recorded checksum",
        kind: "governed_refusal",
      },
    });

    expect(caption).toBe(
      "The botanical-occurrences plane is unavailable: the bytes under the current pointer do not match its recorded checksum"
    );
  });

  it("names the reason when a governed refusal carries no explanation of its own", () => {
    const caption = describeBotanicalOccurrencesState({
      ...SETTLED,
      phase: "error",
      error: {
        error: "The botanical-occurrences plane refused this query",
        reason: "plane_rejected",
        kind: "governed_refusal",
      },
    });

    expect(caption).toBe("The botanical-occurrences plane refused this query (plane_rejected).");
  });

  it("keeps the failure wording for a transport fault, whose detail no reader can act on", () => {
    const caption = describeBotanicalOccurrencesState({
      ...SETTLED,
      phase: "error",
      error: {
        error: "The botanical-occurrences plane could not be reached",
        reason: "request_failed",
        detail: "NetworkError when attempting to fetch resource",
        kind: "transport_fault",
      },
    });

    expect(caption).toBe("Specimen records could not be loaded (request_failed).");
  });

  it("falls back to the failure wording when an older deployment declares no kind", () => {
    // The schema defaults an absent `kind` to `transport_fault`; this pins the same choice in the
    // function itself, so the degrade is to the wording that CLAIMS LESS rather than to quoting a
    // sentence that may not be a refusal explanation.
    const caption = describeBotanicalOccurrencesState({
      ...SETTLED,
      phase: "error",
      error: { error: "Something went wrong", reason: "unrecognized_error" },
    });

    expect(caption).toBe("Specimen records could not be loaded (unrecognized_error).");
  });

  it("still appends the rung-substitution sentence to a governed refusal", () => {
    const caption = describeBotanicalOccurrencesState({
      isStale: false,
      isPartial: false,
      band: "detail",
      servingBand: "grid-0.25",
      phase: "error",
      error: {
        error: "The botanical-occurrences plane is unavailable",
        reason: "no_generation_published",
        detail: "nothing is published for this region",
        kind: "governed_refusal",
      },
    });

    expect(caption).toContain("nothing is published for this region");
    expect(caption).toContain("this view is wider than individual specimen points can answer.");
  });
});
