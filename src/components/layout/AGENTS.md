# Layout shell

## ApplicationShell

Mounted once by `src/app/layout.tsx`, inside `Providers` so `TopBar` can read the
NextAuth session. It decides, per route, whether the global bar exists.

`ROUTES_WITHOUT_GLOBAL_NAVIGATION` is a **deny** list, not an allow list: a new
page is navigable the moment it exists. The entries on it either ship their own
chrome (`/dashboard`), are a focused single-task flow (`/onboarding`, the auth
group, `/invite`, `/join`), or must stay frameless (`/embed`).

When the bar is suppressed the wrapper is `display: contents`, so the route's
layout is byte-for-byte what it was before the bar existed.

## Why the bar is in normal flow, not an overlay

The map is a full-viewport canvas whose floating UI starts at `top-4 left-4`
(`SearchBar`, `MapControls`). A full-width opaque overlay would sit on top of
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

## MapLayout

Pre-existing and owned elsewhere. It is the map route's own shell (side panel,
bottom sheet, full-bleed canvas) and is unrelated to `ApplicationShell`.
