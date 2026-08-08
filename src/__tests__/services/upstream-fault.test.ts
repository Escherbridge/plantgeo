import { describe, expect, it } from "vitest";
import { TRPCError } from "@trpc/server";

import {
  UpstreamHttpError,
  UpstreamPayloadError,
  UpstreamTimeoutError,
} from "@/lib/server/http/bounded-upstream";
import { rethrowUpstreamFault } from "@/lib/server/trpc/upstream-fault";
import { ForecastContractError } from "@/lib/server/services/agri-forecasts";

function capture(error: unknown): unknown {
  try {
    rethrowUpstreamFault(error, "The forecast service");
  } catch (thrown) {
    return thrown;
  }
}

function expectRelabelled(error: unknown) {
  const thrown = capture(error);
  expect(thrown).toBeInstanceOf(TRPCError);
  expect((thrown as TRPCError).code).toBe("SERVICE_UNAVAILABLE");
  expect((thrown as TRPCError).message).toBe("The forecast service is temporarily unavailable");
}

describe("rethrowUpstreamFault", () => {
  it("relabels the transient transport faults as retryable", () => {
    expectRelabelled(new UpstreamTimeoutError("timed out"));
    expectRelabelled(new UpstreamPayloadError("body too large"));
    expectRelabelled(new UpstreamHttpError(429));
    expectRelabelled(new UpstreamHttpError(503));
    // The network-level failure undici raises for an unreachable host.
    expectRelabelled(new TypeError("fetch failed"));
  });

  it("propagates permanent faults unchanged", () => {
    const notFound = new UpstreamHttpError(404);
    expect(capture(notFound)).toBe(notFound);

    // A contract break is permanent until a deploy fixes one side.
    const contract = new ForecastContractError("payload drifted");
    expect(capture(contract)).toBe(contract);

    // A programming TypeError must never be masked as an outage.
    const bug = new TypeError("Cannot read properties of undefined");
    expect(capture(bug)).toBe(bug);
  });
});
