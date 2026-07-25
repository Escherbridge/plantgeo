import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useGeocode } from "@/hooks/useGeocode";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise;
  });
  return { promise, resolve };
}

const result = (name: string, coordinates: [number, number]) => ({
  id: name,
  type: "city",
  name,
  displayName: name,
  coordinates,
  properties: {},
});

describe("useGeocode request ordering", () => {
  it("does not let an older completed request overwrite newer results", async () => {
    vi.useFakeTimers();
    const first = deferred<Response>();
    const second = deferred<Response>();
    vi.mocked(fetch)
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise);
    const { result: hook, rerender } = renderHook(({ query }) => useGeocode(query), {
      initialProps: { query: "denver" },
    });

    await act(async () => {
      vi.advanceTimersByTime(300);
    });
    rerender({ query: "boulder" });
    await act(async () => {
      vi.advanceTimersByTime(300);
    });
    await act(async () => {
      second.resolve(new Response(JSON.stringify({ results: [result("Boulder", [-105.2705, 40.015]) ] })));
      await Promise.resolve();
    });
    await act(async () => {
      first.resolve(new Response(JSON.stringify({ results: [result("Denver", [-104.9903, 39.7392]) ] })));
      await Promise.resolve();
    });

    expect(hook.current.results).toEqual([result("Boulder", [-105.2705, 40.015])]);
    vi.useRealTimers();
  });
});
