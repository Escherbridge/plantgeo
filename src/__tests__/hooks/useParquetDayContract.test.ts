import { renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import {
  useParquetDayContract,
  useParquetFieldDayContract,
} from "@/hooks/useParquetDayContract";
import type { ParquetDayPolicy, ParquetDayRequest } from "@/lib/environmental/parquet-day-contract";
import type { ParquetBrowserReaderResult } from "@/lib/environmental/parquet-presentation";

const TODAY = "2026-09-12";
const YESTERDAY = "2026-09-11";
const SUBJECT = "Test layer";

function ready(requestedDay: string, servedDay = requestedDay): ParquetBrowserReaderResult<unknown> {
  return { state: "ready", requestedDay, servedDay, data: [{ id: "row" }], truncated: false };
}

function dayRequest(
  requestedDay: string,
  policy: ParquetDayPolicy = "exact",
  today = TODAY
): ParquetDayRequest {
  return { requestedDay, policy, today, subject: SUBJECT };
}

function responseQuery(
  data: ParquetBrowserReaderResult<unknown> | undefined,
  isPlaceholderData = false,
  isSuccess = true
) {
  return { data, isPlaceholderData, isSuccess, isFetching: isPlaceholderData };
}

describe("useParquetDayContract accepted response retention", () => {
  it("retains each accepted response under its original day while a new date is pending", () => {
    const previous = ready(YESTERDAY);
    const current = ready(TODAY);
    const { result, rerender } = renderHook(
      ({ query, request }) => useParquetDayContract(query, request),
      { initialProps: { query: responseQuery(previous), request: dayRequest(YESTERDAY) } }
    );
    expect(result.current.data).toBe(previous);

    rerender({ query: responseQuery(previous, true), request: dayRequest(TODAY) });
    expect(result.current.temporalRefused).toBe(false);
    expect(result.current.data).toBe(previous);
    expect(result.current.answeredDate).toBe(YESTERDAY);
    expect(result.current.servedDate).toBe(YESTERDAY);
    expect(result.current.isFetching).toBe(true);

    rerender({ query: responseQuery(current), request: dayRequest(TODAY) });
    expect(result.current.data).toBe(current);
    expect(result.current.answeredDate).toBe(TODAY);

    rerender({ query: responseQuery(current, true), request: dayRequest("2026-09-13") });
    expect(result.current.temporalRefused).toBe(false);
    expect(result.current.data).toBe(current);
    expect(result.current.answeredDate).toBe(TODAY);
  });

  it("refuses a placeholder that this hook never accepted", () => {
    const previous = ready(YESTERDAY);
    const { result } = renderHook(() =>
      useParquetDayContract(responseQuery(previous, true), dayRequest(TODAY))
    );
    expect(result.current.temporalRefused).toBe(true);
    expect(result.current.data).toMatchObject({ state: "upstream_unavailable", fault: { kind: "contract" } });
    expect(result.current.answeredDate).toBeUndefined();
  });

  it("never resurrects a rejected response when the next query uses it as a placeholder", () => {
    const accepted = ready("2026-09-10");
    const rejected = ready(YESTERDAY);
    const { result, rerender } = renderHook(
      ({ query, request }) => useParquetDayContract(query, request),
      { initialProps: { query: responseQuery(accepted), request: dayRequest("2026-09-10") } }
    );
    expect(result.current.data).toBe(accepted);

    rerender({ query: responseQuery(rejected), request: dayRequest(TODAY) });
    expect(result.current.temporalRefused).toBe(true);
    expect(result.current.data).not.toBe(rejected);

    rerender({ query: responseQuery(rejected, true), request: dayRequest("2026-09-13") });
    expect(result.current.temporalRefused).toBe(true);
    expect(result.current.data).not.toBe(rejected);
    expect(result.current.answeredDate).toBeUndefined();
    expect(result.current.servedDate).toBeUndefined();
  });

  it("does not transfer acceptance to another object with the same dates", () => {
    const accepted = ready(YESTERDAY);
    const unverifiedCopy = ready(YESTERDAY);
    const { result, rerender } = renderHook(
      ({ query, request }) => useParquetDayContract(query, request),
      { initialProps: { query: responseQuery(accepted), request: dayRequest(YESTERDAY) } }
    );

    rerender({ query: responseQuery(unverifiedCopy, true), request: dayRequest(TODAY) });
    expect(result.current.temporalRefused).toBe(true);
    expect(result.current.data).not.toBe(unverifiedCopy);
  });

  it("does not record data from a query that has not completed successfully", () => {
    const previous = ready(YESTERDAY);
    const { result, rerender } = renderHook(
      ({ query, request }) => useParquetDayContract(query, request),
      { initialProps: { query: responseQuery(previous, false, false), request: dayRequest(YESTERDAY) } }
    );

    rerender({ query: responseQuery(previous, true), request: dayRequest(TODAY) });
    expect(result.current.temporalRefused).toBe(true);
    expect(result.current.answeredDate).toBeUndefined();
  });

  it("keeps an accepted live window over midnight but refuses the same newly completed historical mismatch", () => {
    const live = ready(YESTERDAY, "2026-09-10");
    const { result, rerender } = renderHook(
      ({ query, request }) => useParquetDayContract(query, request),
      {
        initialProps: {
          query: responseQuery(live),
          request: dayRequest(YESTERDAY, "live-observations", YESTERDAY),
        },
      }
    );
    expect(result.current.temporalRefused).toBe(false);

    rerender({
      query: responseQuery(live, true),
      request: dayRequest(TODAY, "live-observations", TODAY),
    });
    expect(result.current.temporalRefused).toBe(false);
    expect(result.current.data).toBe(live);
    expect(result.current.answeredDate).toBe(YESTERDAY);
    expect(result.current.servedDate).toBe("2026-09-10");

    rerender({
      query: responseQuery(ready(YESTERDAY, "2026-09-10")),
      request: dayRequest(YESTERDAY, "live-observations", TODAY),
    });
    expect(result.current.temporalRefused).toBe(true);
    expect(result.current.servedDate).toBeUndefined();
  });

  it("retains accepted terminal window evidence under the answered endpoint", () => {
    const absence: ParquetBrowserReaderResult<unknown> = {
      state: "not_generated", requestedDay: "2026-09-10", reason: "day_not_written",
    };
    const { result, rerender } = renderHook(
      ({ query, request }) => useParquetDayContract(query, request),
      {
        initialProps: {
          query: responseQuery(absence),
          request: dayRequest(YESTERDAY, "live-observations", YESTERDAY),
        },
      }
    );
    expect(result.current.answeredDate).toBe(YESTERDAY);

    rerender({ query: responseQuery(absence, true), request: dayRequest(TODAY, "live-observations") });
    expect(result.current.data).toBe(absence);
    expect(result.current.temporalRefused).toBe(false);
    expect(result.current.answeredDate).toBe(YESTERDAY);
    expect(result.current.servedDate).toBe("2026-09-10");
    expect(result.current.temporalNotice).toContain("unpublished partition");
  });
});

function field(requestedDay: string, observedDay: string | null = requestedDay, availability = "published") {
  return { requestedDay, observedDay, availability, features: [{ id: "cell" }] };
}

function fieldQuery(data: ReturnType<typeof field>, isPlaceholderData = false) {
  return { data, isPlaceholderData, isSuccess: true };
}

describe("useParquetFieldDayContract accepted response retention", () => {
  it("retains an accepted field's original day across a requested-date change", () => {
    const previous = field(YESTERDAY);
    const { result, rerender } = renderHook(
      ({ query, requestedDay }) => useParquetFieldDayContract(query, requestedDay, SUBJECT),
      { initialProps: { query: fieldQuery(previous), requestedDay: YESTERDAY } }
    );

    rerender({ query: fieldQuery(previous, true), requestedDay: TODAY });
    expect(result.current.data).toBe(previous);
    expect(result.current.temporalRefused).toBe(false);
    expect(result.current.answeredDate).toBe(YESTERDAY);
  });

  it("never turns a rejected field into an accepted placeholder on the next date", () => {
    const rejected = field(YESTERDAY);
    const { result, rerender } = renderHook(
      ({ query, requestedDay }) => useParquetFieldDayContract(query, requestedDay, SUBJECT),
      { initialProps: { query: fieldQuery(rejected), requestedDay: TODAY } }
    );
    expect(result.current.temporalRefused).toBe(true);
    expect(result.current.data).toBeUndefined();

    rerender({ query: fieldQuery(rejected, true), requestedDay: "2026-09-13" });
    expect(result.current.temporalRefused).toBe(true);
    expect(result.current.data).toBeUndefined();
    expect(result.current.isSuccess).toBe(false);
    expect(result.current.answeredDate).toBeUndefined();
  });

  it("refuses a first-seen field placeholder without accepted identity", () => {
    const previous = field(YESTERDAY);
    const { result } = renderHook(() =>
      useParquetFieldDayContract(fieldQuery(previous, true), TODAY, SUBJECT)
    );
    expect(result.current.temporalRefused).toBe(true);
    expect(result.current.data).toBeUndefined();
  });

  it("preserves accepted typed unavailable fields as placeholders", () => {
    const unavailable = field(YESTERDAY, null, "unavailable");
    const { result, rerender } = renderHook(
      ({ query, requestedDay }) => useParquetFieldDayContract(query, requestedDay, SUBJECT),
      { initialProps: { query: fieldQuery(unavailable), requestedDay: YESTERDAY } }
    );

    rerender({ query: fieldQuery(unavailable, true), requestedDay: TODAY });
    expect(result.current.data).toBe(unavailable);
    expect(result.current.temporalRefused).toBe(false);
    expect(result.current.answeredDate).toBe(YESTERDAY);
  });
});
