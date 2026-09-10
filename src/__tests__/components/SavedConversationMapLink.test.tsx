import { render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { readMapFocus } from "@/lib/map/focus-params";

const mocks = vi.hoisted(() => ({ limit: vi.fn(), orderBy: vi.fn() }));
vi.mock("@/lib/server/auth", () => ({ getServerSession: async () => ({ user: { id: "owner" } }) }));
vi.mock("@/lib/server/db", () => ({ db: {
  select: () => ({ from: () => ({ where: () => ({ limit: mocks.limit, orderBy: mocks.orderBy }) }) }),
} }));

import ConversationDetailPage from "@/app/dashboard/conversations/[id]/page";

it("opens a saved analysis at the saved point using the camera reader's URL contract", async () => {
  mocks.limit.mockResolvedValue([{ id: "saved", title: "Snake River", lat: 45.94, lon: -116.78 }]);
  mocks.orderBy.mockResolvedValue([]);
  render(await ConversationDetailPage({ params: Promise.resolve({ id: "saved" }) }));
  const href = screen.getByRole("link", { name: "Open on Map" }).getAttribute("href");
  const url = new URL(href!, "https://plantgeo.example");
  expect(readMapFocus(url.searchParams)).toEqual({ latitude: 45.94, longitude: -116.78, zoom: 13 });
  expect(url.searchParams.has("ai")).toBe(false);
});
