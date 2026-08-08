import { TRPCError } from "@trpc/server";
import {
  UpstreamHttpError,
  UpstreamPayloadError,
  UpstreamTimeoutError,
} from "@/lib/server/http/bounded-upstream";

/**
 * Maps a bounded-fetch transport fault onto the retryable code, shared by every
 * router that proxies an upstream provider. Transient means a retry can plausibly
 * succeed: timeouts, oversized/unparseable bodies, 429/5xx, and the network-level
 * `TypeError: fetch failed` undici raises when the host is unreachable -- the
 * failure an internal service actually produces when it is down. Anything else --
 * a permanent 4xx, a configuration fault, a contract mismatch -- propagates
 * unchanged rather than being relabelled as a temporary outage the client should
 * retry.
 */
export function rethrowUpstreamFault(error: unknown, provider: string): never {
  const isTransient =
    error instanceof UpstreamTimeoutError ||
    error instanceof UpstreamPayloadError ||
    (error instanceof UpstreamHttpError && (error.status === 429 || error.status >= 500)) ||
    // Message-matched so a genuine programming TypeError is never masked as an outage.
    (error instanceof TypeError && error.message.includes("fetch failed"));
  if (isTransient) {
    throw new TRPCError({
      code: "SERVICE_UNAVAILABLE",
      message: `${provider} is temporarily unavailable`,
    });
  }
  throw error;
}
