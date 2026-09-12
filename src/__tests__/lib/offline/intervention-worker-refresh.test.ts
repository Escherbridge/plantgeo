// @vitest-environment node

import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { describe, expect, it, vi } from "vitest";

describe("the shipped service worker with a warm empty intervention tile", () => {
  it("serves cached empty bytes until publication evicts them, then fetches published bytes", async () => {
    type RequestLike = { url: string; method?: string };
    type WorkerEvent = {
      request?: RequestLike;
      data?: { type: string; sourceId: string };
      source?: { postMessage: (message: unknown) => void };
      respondWith?: (response: Promise<Response>) => void;
      waitUntil?: (work: Promise<unknown>) => void;
    };
    const handlers = new Map<string, (event: WorkerEvent) => void>();
    const entries = new Map<string, Response>();
    let published = false;
    const fetch = vi.fn(async () => new Response(published ? "published-mvt" : "", { status: 200 }));
    const cache = {
      match: async (request: RequestLike) => entries.get(request.url)?.clone(),
      put: async (request: RequestLike, response: Response) => { entries.set(request.url, response); },
      keys: async () => [...entries.keys()].map((url) => ({ url })),
      delete: async (request: RequestLike) => entries.delete(request.url),
    };
    runInNewContext(readFileSync("public/sw.js", "utf8"), {
      self: { addEventListener: (event: string, handler: (event: WorkerEvent) => void) => handlers.set(event, handler) },
      caches: { open: async () => cache }, navigator: {}, URL, Response, fetch,
    });
    const url = "https://tiles.custom.example/intervention_tiles/8/41/91";
    const other = "https://plantgeo-martin.example/other_tiles/8/41/91";
    entries.set(other, new Response("keep"));
    const request = async () => {
      let response: Promise<Response> | undefined;
      handlers.get("fetch")!({ request: { url, method: "GET" }, respondWith: (value) => { response = value; } });
      return (await response!).text();
    };
    expect(await request()).toBe("");
    published = true;
    expect(await request()).toBe("");
    expect(fetch).toHaveBeenCalledTimes(1);
    let refresh: Promise<unknown> | undefined;
    const postMessage = vi.fn();
    handlers.get("message")!({
      data: { type: "REFRESH_DYNAMIC_TILES", sourceId: "intervention_tiles" },
      source: { postMessage }, waitUntil: (work) => { refresh = work; },
    });
    expect(refresh).toBeDefined();
    await refresh;
    expect(postMessage).toHaveBeenCalledWith({ type: "REFRESH_DYNAMIC_TILES_COMPLETE", sourceId: "intervention_tiles", dropped: 1 });
    expect(entries.has(other)).toBe(true);
    expect(await request()).toBe("published-mvt");
    expect(fetch).toHaveBeenCalledTimes(2);
  });
});
