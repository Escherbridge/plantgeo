import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  fetchBounded,
  fetchBoundedJson,
  UpstreamAbortedError,
  UpstreamHttpError,
  UpstreamTimeoutError,
} from "@/lib/server/http/bounded-upstream";

/**
 * The cancellation seam, tested at the only layer that touches `fetch`.
 *
 * Every assertion here is about ONE distinction: an abort and a timeout arrive as the same
 * DOMException and mean opposite things. A timeout is a claim about the upstream that a retry may
 * plausibly beat; an abort is a claim about the caller. Collapsing them pages someone for a user
 * who closed a tab, and -- through `parquetUpstreamFailure` -- caches a cancellation as an answer.
 */
const mockedFetch = vi.mocked(globalThis.fetch);

const URL_UNDER_TEST = "https://agri.internal/api/v1/parquet/day";
const BOUNDS = { maxBytes: 1024, timeoutMs: 5_000 };

/** A minimal 200 whose body is readable exactly once, like a real streamed response. */
function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

function abortError(): DOMException {
  return new DOMException("The operation was aborted.", "AbortError");
}

function passedSignal(callIndex = 0): AbortSignal {
  const init = mockedFetch.mock.calls[callIndex][1] as RequestInit;
  return init.signal as AbortSignal;
}

beforeEach(() => {
  mockedFetch.mockReset();
});

describe("fetchBounded cancellation", () => {
  it("combines the caller's signal with the request's own timeout", async () => {
    const controller = new AbortController();
    mockedFetch.mockResolvedValue(jsonResponse({ ok: true }));

    await fetchBounded(URL_UNDER_TEST, { method: "GET" }, { ...BOUNDS, signal: controller.signal });

    // A combined signal, not the caller's own: the timeout must survive a caller that never aborts.
    const signal = passedSignal();
    expect(signal).toBeInstanceOf(AbortSignal);
    expect(signal).not.toBe(controller.signal);
    expect(signal.aborted).toBe(false);
  });

  it("aborts the request as soon as the caller does", async () => {
    const controller = new AbortController();
    mockedFetch.mockResolvedValue(jsonResponse({ ok: true }));

    await fetchBounded(URL_UNDER_TEST, { method: "GET" }, { ...BOUNDS, signal: controller.signal });
    const signal = passedSignal();
    controller.abort();

    expect(signal.aborted).toBe(true);
  });

  it("still sends a timeout signal when the caller has nothing to cancel", async () => {
    mockedFetch.mockResolvedValue(jsonResponse({ ok: true }));

    await fetchBounded(URL_UNDER_TEST, { method: "GET" }, BOUNDS);

    expect(passedSignal()).toBeInstanceOf(AbortSignal);
  });

  it("reports a caller cancellation as an abort, never as a timeout", async () => {
    const controller = new AbortController();
    controller.abort();
    mockedFetch.mockRejectedValue(abortError());

    await expect(
      fetchBounded(URL_UNDER_TEST, { method: "GET" }, { ...BOUNDS, signal: controller.signal })
    ).rejects.toBeInstanceOf(UpstreamAbortedError);
  });

  /**
   * Classification reads the CALLER'S signal, not the DOMException name, so an abort carrying a
   * custom reason -- which react-query and tRPC both do -- still lands on the abort arm.
   */
  it("classifies an abort with a custom reason correctly", async () => {
    const controller = new AbortController();
    controller.abort(new Error("superseded viewport"));
    mockedFetch.mockRejectedValue(controller.signal.reason);

    await expect(
      fetchBounded(URL_UNDER_TEST, { method: "GET" }, { ...BOUNDS, signal: controller.signal })
    ).rejects.toBeInstanceOf(UpstreamAbortedError);
  });

  it("still reports an upstream timeout as a timeout when nobody cancelled", async () => {
    mockedFetch.mockRejectedValue(
      new DOMException("The operation timed out.", "TimeoutError")
    );

    await expect(
      fetchBounded(URL_UNDER_TEST, { method: "GET" }, BOUNDS)
    ).rejects.toBeInstanceOf(UpstreamTimeoutError);
  });

  /**
   * The body streams after the response head resolves, so an abort can land in either place. An
   * unguarded body read would let a bare DOMException escape the taxonomy entirely.
   */
  it("reports an abort raised while the body is still streaming", async () => {
    const controller = new AbortController();
    controller.abort();
    mockedFetch.mockResolvedValue(
      new Response(
        new ReadableStream({
          start(streamController) {
            streamController.error(abortError());
          },
        }),
        { status: 200, headers: { "content-type": "application/json" } }
      )
    );

    await expect(
      fetchBounded(URL_UNDER_TEST, { method: "GET" }, { ...BOUNDS, signal: controller.signal })
    ).rejects.toBeInstanceOf(UpstreamAbortedError);
  });

  it("leaves the status taxonomy alone for a request nobody cancelled", async () => {
    mockedFetch.mockResolvedValue(
      new Response("{}", { status: 503, headers: { "content-type": "application/json" } })
    );

    await expect(
      fetchBoundedJson(URL_UNDER_TEST, { method: "GET" }, BOUNDS)
    ).rejects.toBeInstanceOf(UpstreamHttpError);
  });

  /**
   * `bodyText` lets a caller that recognises the upstream's own error shape (e.g. a 400
   * argument-validation refusal) read it without a second network round-trip. It must ride along
   * on a readable non-2xx body, and never appear when the body couldn't actually be read.
   */
  it("carries the readable body text on a non-2xx UpstreamHttpError", async () => {
    mockedFetch.mockResolvedValue(
      new Response(JSON.stringify({ error: "invalid_tool_arguments", detail: "day: bad value" }), {
        status: 400,
        headers: { "content-type": "application/json" },
      })
    );

    const failure: unknown = await fetchBoundedJson(URL_UNDER_TEST, { method: "GET" }, BOUNDS).catch((error) => error);
    expect(failure).toBeInstanceOf(UpstreamHttpError);
    expect((failure as UpstreamHttpError).status).toBe(400);
    expect((failure as UpstreamHttpError).bodyText).toBe(JSON.stringify({ error: "invalid_tool_arguments", detail: "day: bad value" }));
  });

  it("omits bodyText when the non-2xx body exceeded the byte cap", async () => {
    mockedFetch.mockResolvedValue(
      new Response("x".repeat(BOUNDS.maxBytes + 1), { status: 400, headers: { "content-type": "application/json" } })
    );

    const failure: unknown = await fetchBoundedJson(URL_UNDER_TEST, { method: "GET" }, BOUNDS).catch((error) => error);
    expect(failure).toBeInstanceOf(UpstreamHttpError);
    expect((failure as UpstreamHttpError).bodyText).toBeUndefined();
  });

  /**
   * `bodyText` rides on ~15 `fetchBoundedJson` call sites, most of which just do
   * `console.error(..., err)` on a transport failure -- they never read `.bodyText` themselves.
   * It must stay off every enumerable-key walk (JSON.stringify, Object.keys, console.error's own
   * util.inspect) while remaining readable through the named property for the one caller
   * (`regional-evidence-tools.ts` `argumentErrorDetail`) that actually relays it.
   */
  it("hides bodyText from JSON.stringify and Object.keys while the property itself still relays the upstream's own error shape", async () => {
    const body = JSON.stringify({ error: "invalid_tool_arguments", detail: "day: bad value" });
    mockedFetch.mockResolvedValue(new Response(body, { status: 400, headers: { "content-type": "application/json" } }));

    const failure: unknown = await fetchBoundedJson(URL_UNDER_TEST, { method: "GET" }, BOUNDS).catch((error) => error);
    expect(failure).toBeInstanceOf(UpstreamHttpError);
    const error = failure as UpstreamHttpError;
    expect(JSON.parse(error.bodyText ?? "")).toEqual({ error: "invalid_tool_arguments", detail: "day: bad value" });
    expect(Object.keys(error)).not.toContain("bodyText");
    expect(JSON.stringify(error)).not.toContain("bad value");
  });

  it("caps the retained bodyText at 4 KiB even when the full body is within the byte bound", async () => {
    const oversizedBody = "y".repeat(6_000);
    mockedFetch.mockResolvedValue(new Response(oversizedBody, { status: 400, headers: { "content-type": "application/json" } }));

    const failure: unknown = await fetchBoundedJson(
      URL_UNDER_TEST, { method: "GET" }, { maxBytes: 10_000, timeoutMs: 5_000 }
    ).catch((error) => error);
    expect(failure).toBeInstanceOf(UpstreamHttpError);
    expect((failure as UpstreamHttpError).bodyText).toHaveLength(4_096);
  });

  it("passes the caller's signal down through fetchBoundedJson too", async () => {
    const controller = new AbortController();
    mockedFetch.mockResolvedValue(jsonResponse({ state: "published" }));

    await expect(
      fetchBoundedJson(URL_UNDER_TEST, { method: "GET" }, { ...BOUNDS, signal: controller.signal })
    ).resolves.toEqual({ state: "published" });

    const signal = passedSignal();
    controller.abort();
    expect(signal.aborted).toBe(true);
  });
});
