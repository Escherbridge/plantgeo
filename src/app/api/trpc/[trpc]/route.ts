import { fetchRequestHandler } from "@trpc/server/adapters/fetch";
import { appRouter } from "@/lib/server/trpc/router";
import { createTRPCContext } from "@/lib/server/trpc/init";
import {
  MAX_INGRESS_BODY_BYTES,
  readBoundedBody,
} from "@/lib/server/security/ingress";

/**
 * Headers that describe the framing of the body we just buffered. They are
 * dropped when the bounded body is re-attached so the runtime recomputes them
 * for the buffered bytes rather than carrying a chunked or stale declaration.
 */
const REFRAMED_HEADERS = ["content-length", "transfer-encoding"];

/**
 * Buffer a mutation body under a byte ceiling before tRPC hands it to Zod.
 *
 * `fetchRequestHandler` applies no body limit of its own, and next.config's
 * `bodySizeLimit` only governs Server Actions, so without this every input
 * schema is walked over whatever an unauthenticated caller chose to send. The
 * ceiling is `MAX_INGRESS_BODY_BYTES`, the same one the machine-ingress routes
 * already enforce through `parseBoundedJson` - one number, not two.
 */
async function withBoundedBody(request: Request): Promise<Request | Response> {
  if (request.method !== "POST" || !request.body) return request;

  const body = await readBoundedBody(request, MAX_INGRESS_BODY_BYTES);
  if (!body.ok) {
    return Response.json({ error: { message: body.error } }, { status: body.status });
  }

  const headers = new Headers(request.headers);
  for (const headerName of REFRAMED_HEADERS) headers.delete(headerName);

  return new Request(request.url, {
    method: request.method,
    headers,
    // Re-attached as text: a tRPC body is always UTF-8 JSON, and the original
    // Content-Type is carried over so the handler still sees what was sent.
    body: new TextDecoder().decode(body.bytes),
  });
}

const handler = async (req: Request) => {
  const bounded = await withBoundedBody(req);
  if (bounded instanceof Response) return bounded;

  return fetchRequestHandler({
    endpoint: "/api/trpc",
    req: bounded,
    router: appRouter,
    createContext: createTRPCContext,
  });
};

export { handler as GET, handler as POST };
