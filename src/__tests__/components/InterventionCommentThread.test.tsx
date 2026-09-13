import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * The shared comment thread (Phase 5 / FR-4), mounted on BOTH the map detail
 * modal and `/feed`.
 *
 * What is pinned here: rendered comments (author, timestamp, body), a working
 * compose box for a signed-in viewer, `/feed`'s sign-in gate for a signed-out
 * one, offset paging through `nextOffset`, the author-or-moderator delete
 * affordance, and that a comment body containing markup renders as inert text.
 */

const mocks = vi.hoisted(() => ({
  session: { data: null as unknown, status: "authenticated" as string },
  listCommentsQuery: vi.fn(),
  displayNamesQuery: vi.fn(),
  postMutate: vi.fn(),
  deleteMutate: vi.fn(),
}));

vi.mock("next-auth/react", () => ({
  useSession: () => mocks.session,
}));

vi.mock("@/lib/trpc/client", () => ({
  trpc: {
    users: {
      getDisplayNames: { useQuery: mocks.displayNamesQuery },
    },
    interventionSocial: {
      listComments: { useQuery: mocks.listCommentsQuery },
      postComment: {
        useMutation: (opts?: { onSuccess?: (data: unknown) => void }) => ({
          mutate: (input: { featureId: string; body: string }) => {
            opts?.onSuccess?.(mocks.postMutate(input));
          },
          isPending: false,
        }),
      },
      deleteComment: {
        useMutation: (opts?: {
          onSuccess?: (data: unknown, variables: unknown) => void;
        }) => ({
          mutate: (input: { commentId: string }) => {
            opts?.onSuccess?.(mocks.deleteMutate(input), input);
          },
          isPending: false,
        }),
      },
    },
  },
}));

import { InterventionCommentThread } from "@/components/intervention/InterventionCommentThread";

const FEATURE_ID = "66666666-6666-4666-8666-666666666666";
const AUTHOR_ID = "22222222-2222-4222-8222-222222222222";
const STRANGER_ID = "33333333-3333-4333-8333-333333333333";

const COMMENT = {
  id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1",
  featureId: FEATURE_ID,
  authorUserId: AUTHOR_ID,
  body: "The north edge floods every spring.",
  createdAt: new Date("2026-09-10T12:00:00.000Z"),
};

function signIn(userId: string, platformRole?: string) {
  mocks.session = {
    data: { user: { id: userId, platformRole } },
    status: "authenticated",
  };
}

/** What `users.getDisplayNames` hands back for this render. */
function directory(rows: { id: string; name: string | null; image?: string | null }[]) {
  mocks.displayNamesQuery.mockReturnValue({
    data: rows.map((row) => ({ image: null, ...row })),
    isLoading: false,
    isError: false,
  });
}

/**
 * Every distinct NON-EMPTY `userIds` batch the component asked the directory
 * for. The empty first-render batch is excluded deliberately: it is issued with
 * `enabled: false` (the comments have not arrived yet) and costs no request.
 */
function requestedBatches(): string[] {
  return Array.from(
    new Set(
      mocks.displayNamesQuery.mock.calls
        .map(([input]) => (input as { userIds: string[] }).userIds)
        .filter((userIds) => userIds.length > 0)
        .map((userIds) => JSON.stringify(userIds))
    )
  );
}

function onePage(comments: unknown[], nextOffset: number | null = null) {
  mocks.listCommentsQuery.mockReturnValue({
    data: { comments, nextOffset },
    isLoading: false,
    isError: false,
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  signIn(STRANGER_ID);
  onePage([COMMENT]);
  directory([]);
  mocks.postMutate.mockImplementation(
    (input: { featureId: string; body: string }) => ({
      id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa9",
      featureId: input.featureId,
      authorUserId: STRANGER_ID,
      body: input.body,
      createdAt: new Date("2026-09-11T12:00:00.000Z"),
    })
  );
  mocks.deleteMutate.mockImplementation((input: { commentId: string }) => ({
    id: input.commentId,
    deletedAt: new Date("2026-09-12T00:00:00.000Z"),
  }));
});

afterEach(cleanup);

describe("InterventionCommentThread", () => {
  it("renders each comment's author, timestamp and body", () => {
    render(<InterventionCommentThread featureId={FEATURE_ID} />);

    const item = screen.getByTestId(`intervention-comment-${COMMENT.id}`);
    expect(item.textContent).toContain("The north edge floods every spring.");
    // No `users.name` for this author, so the pre-directory fallback stands: a
    // stable short fragment of the id rather than a raw uuid.
    expect(item.textContent).toContain(AUTHOR_ID.slice(0, 8));
    expect(screen.getByTestId(`intervention-comment-time-${COMMENT.id}`)
      .getAttribute("dateTime")).toBe("2026-09-10T12:00:00.000Z");
  });

  it("names the viewer's own comment as theirs", () => {
    signIn(AUTHOR_ID);
    render(<InterventionCommentThread featureId={FEATURE_ID} />);

    expect(
      screen.getByTestId(`intervention-comment-${COMMENT.id}`).textContent
    ).toContain("You");
  });

  describe("display-name resolution (Phase 4 / FR-4)", () => {
    const SECOND_AUTHOR_ID = "44444444-4444-4444-8444-444444444444";
    const secondComment = {
      ...COMMENT,
      id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa5",
      authorUserId: SECOND_AUTHOR_ID,
      body: "Second author's comment.",
    };

    it("renders the resolved users.name when the directory has one", () => {
      directory([{ id: AUTHOR_ID, name: "Ada Okafor" }]);
      render(<InterventionCommentThread featureId={FEATURE_ID} />);

      const item = screen.getByTestId(`intervention-comment-${COMMENT.id}`);
      expect(item.textContent).toContain("Ada Okafor");
      expect(item.textContent).not.toContain("Contributor");
      expect(item.textContent).not.toContain(AUTHOR_ID);
    });

    it("falls back to the id fragment when the resolved name is null", () => {
      directory([{ id: AUTHOR_ID, name: null }]);
      render(<InterventionCommentThread featureId={FEATURE_ID} />);

      expect(
        screen.getByTestId(`intervention-comment-${COMMENT.id}`).textContent
      ).toContain(`Contributor ${AUTHOR_ID.slice(0, 8)}`);
    });

    it('keeps "You" for the viewer, even when their own name resolves', () => {
      signIn(AUTHOR_ID);
      directory([{ id: AUTHOR_ID, name: "Ada Okafor" }]);
      render(<InterventionCommentThread featureId={FEATURE_ID} />);

      const item = screen.getByTestId(`intervention-comment-${COMMENT.id}`);
      expect(item.textContent).toContain("You");
      expect(item.textContent).not.toContain("Ada Okafor");
    });

    it("asks for the whole author set in ONE batch, not once per comment", () => {
      onePage([COMMENT, secondComment, { ...COMMENT, id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa6" }]);
      directory([
        { id: AUTHOR_ID, name: "Ada Okafor" },
        { id: SECOND_AUTHOR_ID, name: "Bo Lind" },
      ]);
      render(<InterventionCommentThread featureId={FEATURE_ID} />);

      // Three comments, two distinct authors, ONE distinct request payload --
      // and the repeated author appears in it once.
      const batches = requestedBatches();
      expect(batches).toHaveLength(1);
      expect(JSON.parse(batches[0]).sort()).toEqual(
        [AUTHOR_ID, SECOND_AUTHOR_ID].sort()
      );
      expect(screen.getByTestId("intervention-comment-list").textContent).toContain(
        "Bo Lind"
      );
      // The only other payload ever issued is the pre-load empty one, and it is
      // disabled -- so three comments cost exactly one directory request.
      for (const [input, options] of mocks.displayNamesQuery.mock.calls) {
        if ((input as { userIds: string[] }).userIds.length === 0) {
          expect(options).toMatchObject({ enabled: false });
        }
      }
    });

    it("does not issue a directory read for a signed-out viewer", () => {
      mocks.session = { data: null, status: "unauthenticated" };
      render(<InterventionCommentThread featureId={FEATURE_ID} />);

      expect(mocks.displayNamesQuery).toHaveBeenCalledWith(
        { userIds: [] },
        expect.objectContaining({ enabled: false })
      );
    });
  });

  it("posts a comment and shows it without a full remount", () => {
    render(<InterventionCommentThread featureId={FEATURE_ID} />);
    const list = screen.getByTestId("intervention-comment-list");

    fireEvent.change(screen.getByLabelText("Add a comment"), {
      target: { value: "Has anyone surveyed the soil here?" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Post comment" }));

    expect(mocks.postMutate).toHaveBeenCalledWith({
      featureId: FEATURE_ID,
      body: "Has anyone surveyed the soil here?",
    });
    expect(screen.getByTestId("intervention-comment-list")).toBe(list);
    expect(list.textContent).toContain("Has anyone surveyed the soil here?");
    // The box empties so the same text cannot be posted twice by accident.
    expect(
      (screen.getByLabelText("Add a comment") as HTMLTextAreaElement).value
    ).toBe("");
  });

  it("renders a body containing markup as inert text, never as markup", () => {
    const hostile = {
      ...COMMENT,
      id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2",
      body: '<script>window.__pwned = true</script><b>bold</b>',
    };
    onePage([hostile]);
    render(<InterventionCommentThread featureId={FEATURE_ID} />);

    const item = screen.getByTestId(`intervention-comment-${hostile.id}`);
    expect(item.querySelector("script")).toBeNull();
    expect(item.querySelector("b")).toBeNull();
    expect(item.textContent).toContain("<script>window.__pwned = true</script>");
    expect(
      (window as unknown as { __pwned?: boolean }).__pwned
    ).toBeUndefined();
  });

  it("pages through nextOffset with a load-more affordance", () => {
    const second = {
      ...COMMENT,
      id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa3",
      body: "Second page comment.",
    };
    mocks.listCommentsQuery.mockImplementation(
      (input: { offset: number }) =>
        input.offset === 0
          ? { data: { comments: [COMMENT], nextOffset: 1 }, isLoading: false, isError: false }
          : { data: { comments: [second], nextOffset: null }, isLoading: false, isError: false }
    );

    render(<InterventionCommentThread featureId={FEATURE_ID} />);
    fireEvent.click(screen.getByRole("button", { name: "Load more comments" }));

    const list = screen.getByTestId("intervention-comment-list");
    expect(list.textContent).toContain("The north edge floods every spring.");
    expect(list.textContent).toContain("Second page comment.");
    expect(
      screen.queryByRole("button", { name: "Load more comments" })
    ).toBeNull();
  });

  it("gates a signed-out viewer with a sign-in prompt rather than an error", () => {
    mocks.session = { data: null, status: "unauthenticated" };
    render(<InterventionCommentThread featureId={FEATURE_ID} />);

    expect(screen.getByTestId("intervention-comment-signin")).toBeTruthy();
    expect(screen.queryByLabelText("Add a comment")).toBeNull();
    // `listComments` is protected: it must not be issued signed-out.
    expect(mocks.listCommentsQuery).toHaveBeenCalledWith(
      expect.objectContaining({ featureId: FEATURE_ID }),
      expect.objectContaining({ enabled: false })
    );
  });

  describe("delete affordance", () => {
    const deleteName = "Delete comment";

    it("is offered to the comment's own author and removes it from the list", () => {
      signIn(AUTHOR_ID);
      render(<InterventionCommentThread featureId={FEATURE_ID} />);

      fireEvent.click(screen.getByRole("button", { name: deleteName }));

      expect(mocks.deleteMutate).toHaveBeenCalledWith({ commentId: COMMENT.id });
      expect(screen.queryByTestId(`intervention-comment-${COMMENT.id}`)).toBeNull();
    });

    it("is offered to an expert and to an admin moderator", () => {
      signIn(STRANGER_ID, "expert");
      const expertView = render(<InterventionCommentThread featureId={FEATURE_ID} />);
      expect(screen.getByRole("button", { name: deleteName })).toBeTruthy();
      expertView.unmount();

      signIn(STRANGER_ID, "admin");
      render(<InterventionCommentThread featureId={FEATURE_ID} />);
      expect(screen.getByRole("button", { name: deleteName })).toBeTruthy();
    });

    it("is withheld from a viewer who is neither the author nor a moderator", () => {
      signIn(STRANGER_ID, "contributor");
      render(<InterventionCommentThread featureId={FEATURE_ID} />);

      expect(screen.queryByRole("button", { name: deleteName })).toBeNull();
    });
  });
});
