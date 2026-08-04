import { describe, expect, it } from "vitest";

import {
  MAX_INGRESS_BODY_BYTES,
  parseBoundedJson,
  readBoundedBody,
} from "@/lib/server/security/ingress";

/**
 * The byte ceiling every server-side JSON reader shares. `/api/trpc/[trpc]`
 * relies on `readBoundedBody` for exactly this: an unbounded body must be
 * refused before any schema walks it.
 */

function requestWithBody(body: BodyInit, headers: HeadersInit = {}): Request {
  return new Request("https://plantgeo.test/api/trpc/interventions.submitIntervention", {
    method: "POST",
    headers,
    body,
  });
}

/** A body delivered without a Content-Length, the way a chunked upload arrives. */
function chunkedRequest(chunkCount: number, chunkBytes: number): Request {
  const chunk = new Uint8Array(chunkBytes).fill(0x20);
  let remaining = chunkCount;
  const stream = new ReadableStream<Uint8Array>({
    pull(controller) {
      if (remaining-- <= 0) {
        controller.close();
        return;
      }
      controller.enqueue(chunk);
    },
  });
  return new Request("https://plantgeo.test/api/trpc/interventions.submitIntervention", {
    method: "POST",
    body: stream,
    // @ts-expect-error -- `duplex` is required by undici for a streaming body and absent from lib.dom.
    duplex: "half",
  });
}

describe("readBoundedBody", () => {
  it("reads a body that fits under the ceiling", async () => {
    const result = await readBoundedBody(requestWithBody('{"ok":true}'));
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(new TextDecoder().decode(result.bytes)).toBe('{"ok":true}');
  });

  it("refuses a declared Content-Length above the ceiling before reading a byte", async () => {
    const request = requestWithBody('{"ok":true}', {
      "content-length": String(MAX_INGRESS_BODY_BYTES + 1),
    });
    const result = await readBoundedBody(request);
    expect(result).toMatchObject({ ok: false, status: 413 });
  });

  it("refuses a chunked body that outgrows the ceiling mid-stream", async () => {
    // No Content-Length at all: the running total is what stops the allocation.
    const result = await readBoundedBody(chunkedRequest(8, 4_096), 16_384);
    expect(result).toMatchObject({ ok: false, status: 413 });
  });

  it("refuses an empty body rather than returning zero bytes", async () => {
    const result = await readBoundedBody(requestWithBody(""));
    expect(result).toMatchObject({ ok: false, status: 400 });
  });
});

describe("parseBoundedJson", () => {
  it("parses a bounded JSON body", async () => {
    await expect(parseBoundedJson(requestWithBody('{"name":"pilot"}'))).resolves.toEqual({
      ok: true,
      data: { name: "pilot" },
    });
  });

  it("reports invalid JSON as a 400 rather than throwing", async () => {
    await expect(parseBoundedJson(requestWithBody("{not json"))).resolves.toMatchObject({
      ok: false,
      status: 400,
    });
  });
});
