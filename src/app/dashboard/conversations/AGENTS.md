# src/app/dashboard/conversations

Saved AI conversations: the list (up to 50 rows) and a single conversation transcript.

Saved assistant reports pass through `saved-report.ts`, which extends the canonical
remediation report validator with the stored response envelope. Valid reports use
the same `RegionalIntelligenceReport` presentation as the live map conversation,
including AI attribution, evidence labels, recommendations, citations and export.
Only HTTP(S) citation URLs are accepted for this historical rendering boundary.
User text is always text, even if a stored row also contains structured data.
Older or invalid report shapes fall back to the original message with escaped data
under an optional disclosure; opening a conversation never repairs or rewrites it.
The original ownership check and scroll layout remain authoritative.
The page identifies the transcript as saved and retains each creation timestamp.
Rendering old reports does not correct old model claims or imply regenerated evidence.

Open on Map uses `buildMapFocusHref` to focus the saved coordinates through the
existing MapFocus query contract. It does not resume a conversation or trigger
an AI request; location analysis remains an explicit map action.

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

Resume on map is a separate explicit action from Open on Map. It hydrates the owned
saved transcript and conversation ID into the shared store, aborts any abandoned
request, then focuses the original location. It does not call the analysis API;
only a subsequently submitted follow-up does so, replaying server-owned history.
Copied/shared transcripts identify themselves as historical and retain saved times.
Sharing is text-only and user-initiated; the existing ownership rules stay private.
Message feedback attaches to the saved assistant message ID, not its rendered index.
