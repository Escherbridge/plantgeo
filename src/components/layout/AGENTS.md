# Layout shell

## ApplicationShell

Mounted once by `src/app/layout.tsx`, inside `Providers` so `TopBar` can read the
NextAuth session. It decides, per route, whether the global bar exists.

Suppression is a **deny** list, not an allow list: a new page is navigable the
moment it exists. It is split in two because "this page has its own chrome" and
"nothing under here has chrome" are different claims.

`SUBTREES_WITHOUT_GLOBAL_NAVIGATION` matches the route and everything below it.
Those trees are focused single-task flows (`/onboarding`, the auth group,
`/invite`, `/join`) or must stay frameless (`/embed`), children included.

`EXACT_ROUTES_WITHOUT_GLOBAL_NAVIGATION` matches the route and nothing else.
`/dashboard` is the only entry: `dashboard/page.tsx` renders its own menu, but
its children do not, so `/dashboard/org/**` and `/dashboard/conversations/**`
get the global bar. Matching `/dashboard` as a subtree left those pages with
only the links they happened to draw themselves, which on the org settings form
meant the Save button sat below an unscrollable viewport.

When the bar is suppressed the wrapper is `display: contents`, so the route's
layout is byte-for-byte what it was before the bar existed.

## Why the bar is in normal flow, not an overlay

The map is a full-viewport canvas whose floating UI starts at `top-4`
(`TimeDatePill`, and the map manager's own column at `top-4 left-0`). A full-width
opaque overlay would sit on top of
both. So the bar occupies a real `3.5rem` band and the page below it measures the
remainder.

Neither `src/app/page.tsx` nor `MapLayout.tsx` was edited to achieve that. Two
unlayered rules in `globals.css` re-measure any page root that asked for the
whole viewport:

```css
.application-shell-with-top-bar > .h-screen  { height: var(--application-viewport-below-top-bar); }
.application-shell-with-top-bar > .min-h-screen { min-height: ...; }
```

They stay outside `@layer` deliberately — unlayered CSS outranks Tailwind's
`utilities` layer regardless of specificity, which is what lets an untouched
`h-screen` page root keep working. The scope is direct children of the shell,
i.e. page roots only.

This is a compatibility shim, not the intended long-term API. New full-viewport
roots should use the `viewport-below-top-bar` class instead, and the map shell
can adopt it whenever its owner is ready; the shim can then shrink.

`body { overflow: hidden }` is left alone, because the map depends on it.
Document routes therefore scroll inside `EditorialPage` rather than on the body.

## TopBar

`z-index: 40` — above the map's internal `z-30`, below the app's `z-50` dialogs,
sheets and popovers, so an open modal still dims and covers the bar.

Session state comes from `useSession()`, the same hook `UserMenu` and
`TeamSwitcher` already use. Below `md` the nav collapses into a disclosure panel
(`aria-expanded` / `aria-controls`, Escape closes and restores focus to the
toggle, route change closes it). The active route is marked with both
`aria-current="page"` and a heavy accent rule.

### Contrast

The bar is the one place where every editorial surface meets every other, so its
colour choices are load-bearing rather than incidental. Measured against the
tokens in `globals.css`, in both themes:

| Pair | Dark | Light |
| --- | --- | --- |
| `ink-muted` on `paper` (inactive nav) | 6.65:1 | 6.70:1 |
| `accent` on `paper` (focus ring, active rule) | 7.64:1 | 5.76:1 |
| `paper` on `ink` (solid control label + its ring) | 16.5:1 | 15.1:1 |
| `accent-ink` on `accent` (solid control, hover) | 7.64:1 | 5.76:1 |

Three rules follow from that table and must survive any restyle:

1. **The focus ring is tone-aware.** Rings are inset (`-outline-offset-2`) so a
   full-bleed control cannot clip them, which means the ring lands on the
   control's own fill. `accent` on an `ink` fill is 2.16:1 dark / 2.96:1 light —
   below the 3:1 that WCAG 2.2 SC 1.4.11 requires of a focus indicator. Ink- and
   accent-filled controls therefore use `editorialFocusRingInverse`; everything
   sitting on paper uses `editorialFocusRing`.
2. **Hover shifts fill, never opacity.** `hover:opacity-*` dims a control at the
   moment the pointer is on it, which lowers contrast exactly when the user is
   reading it. Solid controls go `bg-ink` → `bg-accent`; nav items thicken their
   rule instead of only changing text colour.
3. **State is never colour alone.** The active route carries `aria-current`, a
   4px accent top rule, and a colour change; hover adds a `rule-faint` top rule.

Nav chrome uses `text-nav` (0.75rem / 0.14em) rather than `text-label`
(0.6875rem / 0.2em): the label token is tuned for captions read in place, and at
that size and tracking a primary nav is legible but effortful.

### The disclosure, and why focus is not trapped

The compact menu is a disclosure, not a modal, so it follows the APG disclosure
pattern rather than the dialog one: Escape closes it and returns focus to the
toggle, and a pointer press or a Tab that leaves the header closes it, but focus
is never trapped and the page behind stays operable. Collapsed, it uses the
`hidden` **attribute** rather than the class, which takes it out of both the tab
order and the accessibility tree; the desktop nav's `hidden md:flex` does the
same by `display: none`, so the two `nav` landmarks are never exposed at once.

## MapLayout

Pre-existing and owned elsewhere. It is the map route's own shell (side panel,
bottom sheet, full-bleed canvas) and is unrelated to `ApplicationShell`.
