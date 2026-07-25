import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { EXPECTED_DRIZZLE_MIGRATION } from "@/lib/server/db/migration-contract";

interface JournalEntry {
  tag: string;
  when: number;
}

describe("readiness migration contract", () => {
  it("matches the latest committed Drizzle migration", () => {
    const journal = JSON.parse(
      readFileSync("drizzle/meta/_journal.json", "utf8")
    ) as { entries: JournalEntry[] };
    const latest = journal.entries.at(-1);
    const migration = readFileSync(
      `drizzle/${EXPECTED_DRIZZLE_MIGRATION.tag}.sql`
    );

    expect(latest).toEqual(
      expect.objectContaining({
        tag: EXPECTED_DRIZZLE_MIGRATION.tag,
        when: EXPECTED_DRIZZLE_MIGRATION.createdAt,
      })
    );
    expect(createHash("sha256").update(migration).digest("hex")).toBe(
      EXPECTED_DRIZZLE_MIGRATION.sha256
    );
  });
});
