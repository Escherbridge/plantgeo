# Editorial design system — Swiss brutalist

A token layer plus a small set of primitives. It exists so screens can adopt the
new visual language **incrementally**: the tokens are additive, and nothing here
changes an existing screen until that screen opts in.

## Why it looks like this

Swiss brutalist *editorial* — the reference is a well-set publication, not a
poster. Strong typographic hierarchy, heavy rules, a strict grid, generous
whitespace, flat high-contrast colour. No corner radius, no soft shadow, no
gradient, anywhere. Restraint is the point: the only decorative voice in the
system is the mono eyebrow label.

## Where the tokens live

`src/styles/globals.css`, in the block headed *Editorial token layer*. Two tiers:

1. **Runtime custom properties** prefixed `--editorial-*`. Colour values are
   redeclared under `.light`; everything else is theme-independent. The platform
   default is dark (`:root`), matching the legacy `--background` block above it.
2. **Tailwind v4 theme** via `@theme inline`. `inline` is required — it makes the
   generated utilities emit `var(--editorial-*)` rather than a frozen value, so
   every utility follows the `.light` toggle with no duplicated CSS.

Tailwind is configured CSS-first. There is no `tailwind.config.js` and adding one
would take the theme out of this file.

## Token API

| Namespace | Utilities | Names |
| --- | --- | --- |
| `--color-*` | `bg-`, `text-`, `border-` | `paper`, `paper-recessed`, `ink`, `ink-muted`, `ink-inverse`, `rule`, `rule-faint`, `accent`, `accent-ink`, `signal` |
| `--text-*` | `text-` (size + leading + tracking together) | `label`, `caption`, `body`, `subheading`, `lead`, `heading`, `title`, `headline`, `display` |
| `--font-*` | `font-` | `editorial-display` (Archivo), `editorial-text` (Newsreader), `editorial-label` (IBM Plex Mono) |
| `--spacing-*` | `p-`, `m-`, `gap-`, `w-`, `h-`, `top-`… | `hairline`, `tight`, `snug`, `regular`, `comfortable`, `roomy`, `section`, `chapter`, `gutter`, `page-inset` |
| `--container-*` | `max-w-` | `page`, `measure`, `measure-narrow` |
| `--leading-*`, `--tracking-*` | `leading-`, `tracking-` | display / headline / title / heading / body / caption / label |

Each `text-*` step carries its own line height and letter spacing, so
`text-display` is a complete typographic instruction, not just a size.

### Rule utilities

Rules are the system's structural ornament, so they get first-class utilities
rather than arbitrary values: `rule-{all,top,bottom,left,right}-{hairline,medium,heavy,massive}`
(1 / 2 / 4 / 10 px). They set **width and style only** — pair them with
`border-*-rule` or `border-*-rule-faint`, because the legacy base layer paints
every border with the old `--border` token.

`editorial-flat` asserts the flatness contract (zero radius, no shadow, no
background image) for components that need to say so explicitly.

## Primitives

- `layout.tsx` — `EditorialPage` (scroll surface sized to the viewport below the
  global bar), `EditorialContainer`, `EditorialGrid` (4 columns, 12 above `md`),
  `EditorialRule`, `EditorialSection` (numbered chapter with a sticky label
  column), `EditorialPanel`, `EditorialColophon`.
- `typography.tsx` — `EditorialEyebrow`, `Display`, `Headline`, `Title`,
  `Heading`, `Subheading`, `Lead`, `Caption`, `Prose`, `PullQuote`,
  `DefinitionList`. All accept `as` to keep the heading outline honest
  independently of the visual step.
- `controls.tsx` — `EditorialLink`, `EditorialActionLink`, `EditorialButton`,
  `EditorialNotice`, `EditorialTag`, plus the shared `editorialFocusRing`.
- `fields.tsx` — `EditorialSelectField`. Carries `"use client"` and is therefore
  **not** re-exported from `index.ts`, which keeps the barrel safe to import from
  a server component.

`EditorialNotice` with `tone="signal"` is reserved for *this data is unavailable
and here is why*. It is never decorative — see the honest-empty-state principle
on `/about`.

## Adopting incrementally

Swap one property at a time: `border-[hsl(var(--border))]` → `border-rule-faint`,
`text-sm` → `text-caption`, `rounded-lg` → nothing. Legacy `--background`,
`--foreground`, `--radius` and friends are untouched and still work, so a screen
can be half-migrated without looking broken.
