"use client";

import { useEffect, useMemo, useState, type FormEvent } from "react";
import { useSession } from "next-auth/react";
import { trpc } from "@/lib/trpc/client";
import { SocialSignInPrompt } from "@/components/intervention/sign-in-gate";
import {
  contributorFallbackLabel,
  useDisplayNames,
} from "@/components/intervention/use-display-names";

/** Exactly `listComments`' projection, restated so the component stays prop-shaped. */
export interface InterventionComment {
  id: string;
  featureId: string;
  authorUserId: string;
  body: string;
  createdAt: Date | string;
}

export interface InterventionCommentThreadProps {
  /** A `geo.features.id`. */
  featureId: string;
  /** Page size handed to `listComments`; paging is by `nextOffset`. */
  pageSize?: number;
  className?: string;
}

/** Roles `deleteComment` accepts as a moderator, mirroring that procedure. */
const MODERATOR_ROLES = ["expert", "admin"];

const DEFAULT_PAGE_SIZE = 20;

/**
 * The shared flat comment thread for a feature (FR-4), mounted on the map
 * detail modal and on `/feed` through the same component.
 *
 * Paging: offset-based `load more`, accumulating pages into local state, since
 * `listComments` returns `nextOffset` (null on the last page). Posting appends
 * the row the server returned and deleting drops it locally -- both mutate the
 * accumulated list in place, so neither remounts the thread or reloads the
 * page around it.
 *
 * Author display (Phase 4 / FR-4): names come from `users.getDisplayNames` via
 * `useDisplayNames`, batched ONCE per distinct set of loaded authors. The
 * viewer's own comment still reads "You" (it is a stronger signal than their
 * own name), and an author whose `users.name` is unset keeps the pre-directory
 * id-fragment fallback.
 *
 * Escaping: every body is rendered as JSX text (`{comment.body}`), never
 * `dangerouslySetInnerHTML`, so markup in a comment is inert text by
 * construction (NFR-2); `InterventionCommentThread.test.tsx` pins that.
 */
export function InterventionCommentThread({
  featureId,
  pageSize = DEFAULT_PAGE_SIZE,
  className,
}: InterventionCommentThreadProps) {
  const { data: session, status } = useSession();
  const authenticated = status === "authenticated";
  const viewer = (session?.user as { id?: string; platformRole?: string } | undefined) ?? {};
  const isModerator = Boolean(
    viewer.platformRole && MODERATOR_ROLES.includes(viewer.platformRole)
  );

  const [offset, setOffset] = useState(0);
  const [loaded, setLoaded] = useState<InterventionComment[]>([]);
  const [nextOffset, setNextOffset] = useState<number | null>(null);
  const [draft, setDraft] = useState("");

  const commentsQuery = trpc.interventionSocial.listComments.useQuery(
    { featureId, limit: pageSize, offset },
    { enabled: authenticated, retry: false }
  );

  const page = commentsQuery.data;

  // The DISTINCT author set of everything loaded so far. Recomputed only when
  // `loaded` changes identity, and de-duplicated again inside the hook, so the
  // directory read is one call per author set rather than one call per comment.
  const authorIds = useMemo(
    () => Array.from(new Set(loaded.map((comment) => comment.authorUserId))),
    [loaded]
  );
  const directory = useDisplayNames(authorIds);

  useEffect(() => {
    if (!page) return;
    // Functional merge returning the SAME array when the page adds nothing:
    // React bails out of the re-render, so a query hook that hands back a fresh
    // object every render cannot spin this effect.
    setLoaded((previous) => mergeById(previous, page.comments as InterventionComment[]));
    setNextOffset(page.nextOffset ?? null);
  }, [page]);

  const postComment = trpc.interventionSocial.postComment.useMutation({
    onSuccess: (posted) => {
      if (posted) {
        setLoaded((previous) => mergeById(previous, [posted as InterventionComment]));
      }
      setDraft("");
    },
  });

  const deleteComment = trpc.interventionSocial.deleteComment.useMutation({
    onSuccess: (_data, variables) => {
      const commentId = (variables as { commentId: string }).commentId;
      setLoaded((previous) => previous.filter((comment) => comment.id !== commentId));
    },
  });

  if (!authenticated) {
    return (
      <section className={className} aria-label="Comments">
        <h3 className="text-xs font-medium">Comments</h3>
        <div className="mt-2">
          <SocialSignInPrompt
            testId="intervention-comment-signin"
            message="Comments on a proposal are shown to signed-in accounts only."
          />
        </div>
      </section>
    );
  }

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const body = draft.trim();
    if (!body || postComment.isPending) return;
    postComment.mutate({ featureId, body });
  };

  return (
    <section className={className} aria-label="Comments">
      <h3 className="text-xs font-medium">Comments</h3>

      <ul data-testid="intervention-comment-list" className="mt-2 space-y-2">
        {loaded.map((comment) => (
          <CommentRow
            key={comment.id}
            comment={comment}
            authorLabel={authorLabel(
              comment.authorUserId,
              comment.authorUserId === viewer.id,
              directory.nameFor(comment.authorUserId)
            )}
            canDelete={comment.authorUserId === viewer.id || isModerator}
            onDelete={() => deleteComment.mutate({ commentId: comment.id })}
          />
        ))}
      </ul>

      {commentsQuery.isError ? (
        <p role="alert" className="mt-2 text-xs">
          Comments could not be loaded.
        </p>
      ) : null}
      {!commentsQuery.isError && loaded.length === 0 && !commentsQuery.isLoading ? (
        <p className="mt-2 text-xs text-[hsl(var(--muted-foreground))]">
          No comments yet.
        </p>
      ) : null}

      {nextOffset !== null ? (
        <button
          type="button"
          onClick={() => setOffset(nextOffset)}
          className="mt-2 rounded border border-[hsl(var(--border))] px-2 py-1 text-xs"
        >
          Load more comments
        </button>
      ) : null}

      <form onSubmit={submit} className="mt-3 space-y-2">
        <label htmlFor={`comment-box-${featureId}`} className="block text-xs">
          Add a comment
        </label>
        <textarea
          id={`comment-box-${featureId}`}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          rows={2}
          maxLength={2000}
          className="w-full rounded border border-[hsl(var(--border))] bg-transparent px-2 py-1 text-xs"
        />
        <button
          type="submit"
          disabled={postComment.isPending || draft.trim().length === 0}
          className="rounded border border-[hsl(var(--border))] px-2 py-1 text-xs disabled:opacity-60"
        >
          Post comment
        </button>
        {postComment.isError ? (
          <p role="alert" className="text-xs">
            That comment could not be posted.
          </p>
        ) : null}
      </form>
    </section>
  );
}

function CommentRow({
  comment,
  authorLabel,
  canDelete,
  onDelete,
}: {
  comment: InterventionComment;
  /** Already resolved by the parent, which owns the one directory read. */
  authorLabel: string;
  canDelete: boolean;
  onDelete: () => void;
}) {
  const posted =
    comment.createdAt instanceof Date
      ? comment.createdAt
      : new Date(comment.createdAt);
  const iso = Number.isNaN(posted.getTime()) ? undefined : posted.toISOString();

  return (
    <li
      data-testid={`intervention-comment-${comment.id}`}
      className="rounded border border-[hsl(var(--border))] px-2 py-1 text-xs"
    >
      <div className="flex items-baseline gap-2">
        <span className="font-medium">{authorLabel}</span>
        {iso ? (
          <time
            data-testid={`intervention-comment-time-${comment.id}`}
            dateTime={iso}
            className="text-[hsl(var(--muted-foreground))]"
          >
            {posted.toLocaleString()}
          </time>
        ) : null}
        {canDelete ? (
          <button
            type="button"
            onClick={onDelete}
            aria-label="Delete comment"
            className="ml-auto rounded border border-[hsl(var(--border))] px-1 text-[10px]"
          >
            Delete comment
          </button>
        ) : null}
      </div>
      {/* Plain JSX text: React escapes it, so markup in a body is inert. */}
      <p className="mt-1 whitespace-pre-wrap break-words">{comment.body}</p>
    </li>
  );
}

/**
 * "You" wins over the viewer's own resolved name (sub-decision (c)); anyone
 * else is their `users.name`, falling back to the id fragment when unset.
 */
export function authorLabel(
  authorUserId: string,
  isOwn: boolean,
  resolvedName: string | null
): string {
  if (isOwn) return "You";
  return resolvedName ?? contributorFallbackLabel(authorUserId);
}

/** Append only the rows not already held, preserving order and identity. */
function mergeById(
  previous: InterventionComment[],
  incoming: InterventionComment[]
): InterventionComment[] {
  const held = new Set(previous.map((comment) => comment.id));
  const additions = incoming.filter((comment) => !held.has(comment.id));
  return additions.length === 0 ? previous : [...previous, ...additions];
}
