import { NextRequest, NextResponse } from "next/server";
import { createHash, randomBytes } from "crypto";
import { db } from "@/lib/server/db";
import { apiKeys } from "@/lib/server/db/schema";
import { eq } from "drizzle-orm";
import { getServerSession } from "@/lib/server/auth";

/**
 * Verify the request is from an authenticated admin.
 * Accepts either a next-auth session with platformRole 'admin'
 * or an Authorization: Bearer token matching ADMIN_API_TOKEN env var.
 */
async function requireAdmin(request: NextRequest): Promise<boolean> {
  // Check Bearer token against env var (for service-to-service calls)
  const authHeader = request.headers.get("authorization");
  if (authHeader?.startsWith("Bearer ")) {
    const token = authHeader.slice(7);
    const adminToken = process.env.ADMIN_API_TOKEN;
    if (adminToken && token === adminToken) {
      return true;
    }
  }

  // Check next-auth session
  const session = await getServerSession();
  if (
    session &&
    (session as { user?: { platformRole?: string } }).user?.platformRole ===
      "admin"
  ) {
    return true;
  }

  return false;
}

/**
 * POST /api/v1/admin/keys
 * Create a new API key. Returns the raw key once — never stored.
 *
 * Body (JSON):
 *   { name: string, permissions?: string[], rateLimit?: number, userId?: string, teamId?: string }
 *
 * Response:
 *   { id, name, key, permissions, rateLimit, createdAt }
 */
export async function POST(request: NextRequest) {
  const isAdmin = await requireAdmin(request);
  if (!isAdmin) {
    return NextResponse.json({ error: "Forbidden" }, { status: 403 });
  }

  let body: {
    name?: string;
    permissions?: string[];
    rateLimit?: number;
    userId?: string;
    teamId?: string;
  };

  try {
    body = (await request.json()) as typeof body;
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  const name = body.name?.trim();
  if (!name) {
    return NextResponse.json(
      { error: "Missing required field: name" },
      { status: 400 }
    );
  }

  // Generate a cryptographically random 32-byte key, hex-encoded (64 chars)
  const rawKey = randomBytes(32).toString("hex");
  const keyHash = createHash("sha256").update(rawKey).digest("hex");

  const [record] = await db
    .insert(apiKeys)
    .values({
      keyHash,
      name,
      permissions: body.permissions ?? ["read:context", "read:teams"],
      rateLimit: body.rateLimit ?? 100,
      userId: body.userId ?? null,
      teamId: body.teamId ?? null,
    })
    .returning({
      id: apiKeys.id,
      name: apiKeys.name,
      permissions: apiKeys.permissions,
      rateLimit: apiKeys.rateLimit,
    });

  return NextResponse.json(
    {
      id: record.id,
      name: record.name,
      key: rawKey, // Raw key returned once — never stored
      permissions: record.permissions,
      rateLimit: record.rateLimit,
      createdAt: new Date().toISOString(),
    },
    { status: 201 }
  );
}

/**
 * DELETE /api/v1/admin/keys?id={keyId}
 * Revoke (delete) an API key by ID.
 */
export async function DELETE(request: NextRequest) {
  const isAdmin = await requireAdmin(request);
  if (!isAdmin) {
    return NextResponse.json({ error: "Forbidden" }, { status: 403 });
  }

  const keyId = request.nextUrl.searchParams.get("id");
  if (!keyId) {
    return NextResponse.json(
      { error: "Missing required query parameter: id" },
      { status: 400 }
    );
  }

  const deleted = await db
    .delete(apiKeys)
    .where(eq(apiKeys.id, keyId))
    .returning({ id: apiKeys.id });

  if (deleted.length === 0) {
    return NextResponse.json({ error: "API key not found" }, { status: 404 });
  }

  return NextResponse.json({ deleted: true, id: keyId });
}
