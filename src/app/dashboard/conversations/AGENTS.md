# src/app/dashboard/conversations

Saved AI conversations: the list (up to 50 rows) and a single conversation transcript.

- `/dashboard` is an *exact-match* exemption in `ApplicationShell`, so this
  subtree renders under the 3.5rem `TopBar` while `/dashboard` itself does not.
  `globals.css` keeps `body { overflow: hidden }` for the map, so the viewport
  never scrolls and this subtree has to own its own scroll surface — without
  `layout.tsx` the tail of a 50-row list is simply unreachable.
- The scroll element needs `min-h-0` alongside `flex-1 overflow-y-auto`: in a
  flex column a child's default `min-height: auto` refuses to shrink below its
  content, which silently defeats the overflow.
- The pages supply their own `mx-auto max-w-4xl p-6` measure, so the layout's
  scroll container deliberately adds no padding of its own.
