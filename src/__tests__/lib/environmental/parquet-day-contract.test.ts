import { describe, expect, it } from "vitest";
import {
  withParquetDayContract,
  withParquetFieldDayContract,
  type ParquetDayPolicy,
} from "@/lib/environmental/parquet-day-contract";
import type { ParquetBrowserReaderResult } from "@/lib/environmental/parquet-presentation";

const TODAY = "2026-09-12";
const YESTERDAY = "2026-09-11";
const SUBJECT = "Test layer";

function ready(requestedDay = TODAY, servedDay = requestedDay) {
  return {
    state: "ready" as const,
    requestedDay,
    servedDay,
    data: [{ id: "published-row" }],
    truncated: false,
  };
}

function absent(requestedDay = TODAY, servedDay = requestedDay) {
  return {
    state: "absent" as const,
    requestedDay,
    servedDay,
    evidence: {
      reason: "source_checked_empty",
      upstreamResponse: "No observations reported",
      recordedAt: `${requestedDay}T12:00:00Z`,
      runId: "test-publication",
    },
  };
}

function guard(
  data: ParquetBrowserReaderResult<unknown> | undefined,
  policy: ParquetDayPolicy = "exact",
  requestedDay: string | undefined = TODAY,
  isPlaceholderData = false,
  retainedRequest?: {
    requestedDay: string;
    policy: ParquetDayPolicy;
    subject: string;
    today: string;
  }
) {
  return withParquetDayContract(
    { data, isPlaceholderData, isFetching: isPlaceholderData },
    { policy, requestedDay, subject: SUBJECT, today: TODAY, retainedRequest }
  );
}

describe("withParquetDayContract", () => {
  it("preserves a matching exact response and its query metadata", () => {
    const result = ready();
    const query = guard(result);

    expect(query.data).toBe(result);
    expect(query.temporalRefused).toBe(false);
    expect(query.temporalNotice).toBeNull();
    expect(query.answeredDate).toBe(TODAY);
    expect(query.servedDate).toBe(TODAY);
    expect(query.isFetching).toBe(false);
  });

  it.each([
    [YESTERDAY, TODAY],
    [TODAY, YESTERDAY],
    [TODAY, "2026-09-13"],
  ])("refuses an exact response requested %s and served %s", (requested, served) => {
    const query = guard(ready(requested, served));

    expect(query.temporalRefused).toBe(true);
    expect(query.data).toMatchObject({ state: "upstream_unavailable", fault: { kind: "contract" } });
    expect(query.temporalNotice).toContain(TODAY);
    expect(query.temporalNotice).toContain("not shown");
    expect(query.answeredDate).toBeUndefined();
    expect(query.servedDate).toBeUndefined();
  });

  it.each(["2026-02-29", "2026-13-01", "2026-9-12"])(
    "refuses invalid calendar request %s even when the response echoes it",
    (invalid) => {
      expect(guard(ready(invalid), "exact", invalid).temporalRefused).toBe(true);
    }
  );

  it("refuses an invalid served day instead of treating it as matching an as-of range", () => {
    expect(guard(ready(TODAY, "2026-02-30"), "snapshot").temporalRefused).toBe(true);
  });

  it.each([
    [TODAY, false],
    ["2026-08-29", false],
    ["2026-08-28", true],
    ["2026-09-13", true],
  ])("enforces the inclusive 14-day release boundary for %s", (servedDay, refused) => {
    const result = ready(TODAY, servedDay);
    const query = guard(result, "release");

    expect(query.temporalRefused).toBe(refused);
    if (!refused) {
      expect(query.data).toBe(result);
      expect(query.answeredDate).toBe(TODAY);
      expect(query.servedDate).toBe(servedDay);
      if (servedDay !== TODAY) {
        expect(query.temporalNotice).toContain(`requested ${TODAY}`);
        expect(query.temporalNotice).toContain(`served for ${servedDay}`);
      }
    }
  });

  it("requires the selected-day echo even for an otherwise valid release carry", () => {
    const query = guard(ready(YESTERDAY, "2026-08-29"), "release");
    expect(query.temporalRefused).toBe(true);
    expect(query.temporalNotice).toContain(`response requested ${YESTERDAY}`);
  });

  it("allows an older as-of snapshot with a notice but refuses a future snapshot", () => {
    const result = ready(TODAY, "2013-01-18");
    const query = guard(result, "snapshot");

    expect(query.data).toBe(result);
    expect(query.servedDate).toBe("2013-01-18");
    expect(query.answeredDate).toBe(TODAY);
    expect(query.temporalNotice).toContain("served for 2013-01-18");
    expect(guard(ready(TODAY, "2026-09-13"), "snapshot").temporalRefused).toBe(true);
  });

  it.each([
    ["2026-08-14", false],
    ["2026-08-13", true],
    ["2026-09-13", true],
  ])("enforces the inclusive 29-day vegetation lag for %s", (servedDay, refused) => {
    const result = ready(TODAY, servedDay);
    const query = guard(result, "vegetation-window");

    expect(query.temporalRefused).toBe(refused);
    if (!refused) {
      expect(query.data).toBe(result);
      expect(query.temporalNotice).toContain(servedDay);
      expect(query.answeredDate).toBe(TODAY);
    }
  });

  it("does not accept a ready window result whose request echoes only an earlier partition", () => {
    expect(guard(ready(YESTERDAY), "vegetation-window").temporalRefused).toBe(true);
  });

  it.each([
    [TODAY, TODAY, false],
    [TODAY, YESTERDAY, false],
    [TODAY, "2026-09-10", true],
    [TODAY, "2026-09-13", true],
    [YESTERDAY, YESTERDAY, false],
    [YESTERDAY, "2026-09-10", true],
  ])("validates live/historical request %s served %s", (requestedDay, servedDay, refused) => {
    const result = ready(requestedDay, servedDay);
    const query = guard(result, "live-observations", requestedDay);

    expect(query.temporalRefused).toBe(refused);
    if (!refused) {
      expect(query.data).toBe(result);
      expect(query.answeredDate).toBe(requestedDay);
      expect(query.servedDate).toBe(servedDay);
      if (servedDay !== requestedDay) expect(query.temporalNotice).toContain(servedDay);
    }
  });

  it.each([
    ["vegetation-window", "2026-08-14"],
    ["live-observations", YESTERDAY],
  ] as const)("preserves governed absence at the %s window boundary", (policy, partitionDay) => {
    const result = absent(partitionDay);
    const query = guard(result, policy);

    expect(query.data).toBe(result);
    expect(query.temporalRefused).toBe(false);
    expect(query.answeredDate).toBe(TODAY);
    expect(query.servedDate).toBe(partitionDay);
    expect(query.temporalNotice).toContain("governed absence");
    expect(query.temporalNotice).toContain(partitionDay);
  });

  it.each([
    ["live-observations", YESTERDAY, "day_not_written"],
    ["live-observations", YESTERDAY, "lane_never_written"],
    ["vegetation-window", "2026-08-14", "day_not_written"],
    ["vegetation-window", "2026-08-14", "lane_never_written"],
  ] as const)(
    "preserves %s partition %s with typed %s refusal",
    (policy, partitionDay, reason) => {
      const result = { state: "not_generated" as const, requestedDay: partitionDay, reason };
      const query = guard(result, policy);

      expect(query.data).toBe(result);
      expect(query.temporalRefused).toBe(false);
      expect(query.answeredDate).toBe(TODAY);
      expect(query.servedDate).toBe(partitionDay);
      expect(query.temporalNotice).toContain("unpublished partition");
    }
  );

  it("refuses governed-absence evidence served after its own requested partition", () => {
    expect(guard(absent(YESTERDAY, TODAY), "live-observations").temporalRefused).toBe(true);
    expect(guard(absent(YESTERDAY, TODAY), "vegetation-window").temporalRefused).toBe(true);
  });

  it.each([
    ["vegetation-window", "2026-08-13"],
    ["live-observations", "2026-09-10"],
    ["live-observations", "2026-09-13"],
  ] as const)("refuses a terminal partition outside the %s window at %s", (policy, day) => {
    expect(guard(absent(day), policy).temporalRefused).toBe(true);
    expect(guard({ state: "not_generated", requestedDay: day, reason: "day_not_written" }, policy)
      .temporalRefused).toBe(true);
  });

  it("preserves valid old placeholder data under its own date while the new request is pending", () => {
    const result = ready(YESTERDAY);
    const query = guard(result, "exact", TODAY, true, {
      requestedDay: YESTERDAY, policy: "exact", subject: SUBJECT, today: YESTERDAY,
    });

    expect(query.data).toBe(result);
    expect(query.temporalRefused).toBe(false);
    expect(query.answeredDate).toBe(YESTERDAY);
    expect(query.servedDate).toBe(YESTERDAY);
    expect(query.isPlaceholderData).toBe(true);
    expect(query.isFetching).toBe(true);
  });

  it("refuses placeholder data that violates its own date contract", () => {
    const query = guard(ready(YESTERDAY, "2026-09-10"), "exact", TODAY, true, {
      requestedDay: YESTERDAY, policy: "exact", subject: SUBJECT, today: YESTERDAY,
    });
    expect(query.temporalRefused).toBe(true);
    expect(query.answeredDate).toBeUndefined();
    expect(query.servedDate).toBeUndefined();
  });

  it("refuses a first-seen placeholder without its original accepted request", () => {
    const query = guard(ready(YESTERDAY), "exact", TODAY, true);
    expect(query.temporalRefused).toBe(true);
    expect(query.data).toMatchObject({ state: "upstream_unavailable", fault: { kind: "contract" } });
    expect(query.answeredDate).toBeUndefined();
  });

  it("validates placeholder request echo against the retained request instead of trusting itself", () => {
    const query = guard(ready(YESTERDAY), "exact", TODAY, true, {
      requestedDay: "2026-09-10", policy: "exact", subject: SUBJECT, today: YESTERDAY,
    });
    expect(query.temporalRefused).toBe(true);
    expect(query.answeredDate).toBeUndefined();
  });

  it("retains an accepted live response over UTC midnight using its original live day", () => {
    const result = ready(YESTERDAY, "2026-09-10");
    const query = guard(result, "live-observations", TODAY, true, {
      requestedDay: YESTERDAY, policy: "live-observations", subject: SUBJECT, today: YESTERDAY,
    });
    expect(query.data).toBe(result);
    expect(query.temporalRefused).toBe(false);
    expect(query.answeredDate).toBe(YESTERDAY);
    expect(query.servedDate).toBe("2026-09-10");
    expect(guard(result, "live-observations", YESTERDAY).temporalRefused).toBe(true);
  });

  it("passes an undated static lookup through using its valid as-of relationship", () => {
    const result = ready(TODAY, "2013-01-18");
    const query = withParquetDayContract(
      { data: result },
      { requestedDay: undefined, policy: "static", subject: SUBJECT, today: TODAY }
    );

    expect(query.data).toBe(result);
    expect(query.temporalRefused).toBe(false);
    expect(query.answeredDate).toBe(TODAY);
    expect(query.servedDate).toBe("2013-01-18");
    expect(query.temporalNotice).toBeNull();
    expect(guard(ready(TODAY, "2026-09-13"), "static").temporalRefused).toBe(true);
  });

  it("preserves exact unavailable evidence and transport failures without manufacturing data", () => {
    const results: ParquetBrowserReaderResult<unknown>[] = [
      absent(),
      { state: "not_generated", requestedDay: TODAY, reason: "day_not_written" },
      { state: "not_generated", requestedDay: TODAY, reason: "lane_never_written" },
      { state: "upstream_unavailable", fault: { kind: "timeout", message: "Request timed out" } },
    ];
    for (const result of results) {
      const query = guard(result);
      expect(query.data).toBe(result);
      expect(query.temporalRefused).toBe(false);
      expect(query.temporalNotice).toBeNull();
    }
    expect(guard(undefined).data).toBeUndefined();
    expect(guard(undefined).answeredDate).toBeUndefined();
  });
});

describe("withParquetFieldDayContract", () => {
  const field = {
    requestedDay: TODAY,
    observedDay: TODAY,
    availability: "published",
    features: [{ id: "field-cell" }],
  };

  it("keeps a matching published field and success state", () => {
    const query = withParquetFieldDayContract({ data: field, isSuccess: true }, TODAY, SUBJECT);
    expect(query.data).toBe(field);
    expect(query.isSuccess).toBe(true);
    expect(query.temporalRefused).toBe(false);
    expect(query.temporalNotice).toBeNull();
    expect(query.answeredDate).toBe(TODAY);
  });

  it.each([
    { requestedDay: YESTERDAY, observedDay: YESTERDAY },
    { requestedDay: TODAY, observedDay: YESTERDAY },
    { requestedDay: TODAY, observedDay: null },
  ])("refuses a published field's mismatched request or observation $observedDay", (days) => {
    const query = withParquetFieldDayContract(
      { data: { ...field, ...days }, isSuccess: true }, TODAY, SUBJECT
    );

    expect(query.data).toBeUndefined();
    expect(query.isSuccess).toBe(false);
    expect(query.temporalRefused).toBe(true);
    expect(query.temporalNotice).toContain("not shown");
    expect(query.answeredDate).toBeUndefined();
  });

  it.each(["not_published", "request_failed"])(
    "preserves typed unavailable field reason %s with no observed day",
    (reason) => {
      const result = { ...field, availability: "unavailable", reason, observedDay: null, features: [] };
      const query = withParquetFieldDayContract({ data: result, isSuccess: true }, TODAY, SUBJECT);

      expect(query.data).toBe(result);
      expect(query.temporalRefused).toBe(false);
      expect(query.answeredDate).toBe(TODAY);
      expect(query.isSuccess).toBe(true);
    }
  );

  it("refuses unavailable field evidence belonging to a different request", () => {
    const query = withParquetFieldDayContract({
      data: { ...field, requestedDay: YESTERDAY, observedDay: null, availability: "unavailable" },
      isSuccess: true,
    }, TODAY, SUBJECT);

    expect(query.temporalRefused).toBe(true);
    expect(query.data).toBeUndefined();
    expect(query.isSuccess).toBe(false);
  });

  it("retains a placeholder's own day but refuses its invalid observation day", () => {
    const result = { ...field, requestedDay: YESTERDAY, observedDay: YESTERDAY };
    const query = withParquetFieldDayContract(
      { data: result, isPlaceholderData: true, isSuccess: true }, TODAY, SUBJECT, YESTERDAY
    );
    expect(query.data).toBe(result);
    expect(query.answeredDate).toBe(YESTERDAY);
    expect(query.isSuccess).toBe(true);

    const invalid = withParquetFieldDayContract(
      { data: { ...result, observedDay: TODAY }, isPlaceholderData: true, isSuccess: true },
      TODAY, SUBJECT, YESTERDAY
    );
    expect(invalid.temporalRefused).toBe(true);
    expect(invalid.data).toBeUndefined();
  });

  it("refuses a first-seen field placeholder without the original accepted day", () => {
    const query = withParquetFieldDayContract(
      { data: field, isPlaceholderData: true, isSuccess: true }, TODAY, SUBJECT
    );
    expect(query.temporalRefused).toBe(true);
    expect(query.data).toBeUndefined();
    expect(query.isSuccess).toBe(false);
  });

  it("leaves an unresolved field unresolved", () => {
    const query = withParquetFieldDayContract({ data: undefined, isSuccess: false }, TODAY, SUBJECT);
    expect(query.data).toBeUndefined();
    expect(query.isSuccess).toBe(false);
    expect(query.temporalRefused).toBe(false);
    expect(query.answeredDate).toBeUndefined();
  });
});
