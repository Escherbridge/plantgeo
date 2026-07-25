---
type: code-styleguide
---

# PlantGeo UI, JSX, and CSS Standard

This standard governs JSX/TSX, Tailwind utilities, and `src/styles/**/*.css`.
PlantGeo is an operational map: clarity, accessibility, provenance, and safe
human control matter more than visual novelty. Use semantic HTML and the shared
UI primitives before introducing a custom implementation.

## Structure and shared UI

- Use native semantic elements first: `button` for actions, `a` for navigation,
  `label` for controls, `input`/`select` for input, headings in order, and
  lists/tables for comparable results. A clickable `div` or `span` is not an
  acceptable control.
- Build on `src/components/ui` primitives and the token system in
  `src/styles/globals.css`. Extend a primitive or add a named variant when a
  pattern recurs; do not copy a long run of utility classes across map panels.
- Keep the page shell, map canvas, map controls, side panels, dialogs, and
  toasts separate in the DOM and z-index plan. Do not create an arbitrary z
  index to cover a broken layer order. New overlays must declare their owner,
  dismissal behavior, focus behavior, and mobile placement.
- Use a real `<form>` for a submission flow. Buttons must set `type` explicitly
  when inside a form. Destructive and consequential operations need distinct
  visual treatment and a confirmation step with the precise target and impact.
- Images need purposeful `alt` text; decorative images use empty `alt`. Icon
  buttons need an accessible name. Do not rely on a tooltip as the only label.

## Keyboard, focus, and assistive technology

- Every interactive control must be reachable and usable with a keyboard,
  including controls floating over the map. Keep a visible, high-contrast focus
  indicator using the shared focus style; never remove focus outline without a
  replacement.
- Dialogs, sheets, command palettes, menus, and popovers must move focus in on
  open, trap it when modal, restore it on close, close with Escape when safe,
  and expose the correct ARIA role, name, and description. Prefer an existing
  primitive that already implements this behavior.
- Announce meaningful asynchronous changes through a concise `aria-live`
  region: request started/finished, errors, data unavailable, and successful
  action confirmation. Do not announce every streamed token, cursor movement,
  or map redraw.
- The map canvas is not a sufficient accessibility surface. Provide keyboard
  alternatives for map actions and a panel/list/table view for selected
  features, search results, active layers, and recommended actions. Expose
  geographic coordinates in a readable format, not colour or position alone.
- Support pointer, touch, keyboard, and reduced-motion preferences. Do not make
  hover the only way to inspect data; honor `prefers-reduced-motion` and provide
  a non-animated alternative for pulsing hazards or moving markers.

## Operational clarity and user control

- Give each layer a plain-language name, source/attribution, observed or
  published time, refresh/stale state, spatial coverage, legend, units, and
  uncertainty or limitations where relevant. A risk colour alone never
  communicates severity or confidence.
- Clearly distinguish observed data, modeled estimates, AI recommendations,
  cached data, missing data, and user-entered data. An unavailable source must
  be visible as unavailable; never render it as a normal zero or a blank map
  without explanation.
- AI recommendations must show their supporting signals and freshness, a
  recommendation/not-guarantee notice, and the next safe action. An AI reply
  cannot make a map edit, alert, outreach, or deployment by itself. Put an
  explicit review-and-confirm control in front of each consequential action.
- Ask for location or sensitive data only in context, explain the purpose, show
  the current sharing scope, and let the user cancel or change it. Avoid
  displaying unnecessarily precise locations for protected species, private
  land, or people.
- Loading, empty, partial, stale, permission-denied, rate-limited, offline, and
  error states need specific copy and an appropriate recovery action. Use a
  retry only when the action is safe to repeat; retain form input after a
  recoverable failure.

## Visual system and responsive layout

- Use semantic colour tokens such as `background`, `foreground`, `muted`,
  `destructive`, `border`, and `ring` from `globals.css`, including their dark
  mode values. Do not introduce arbitrary hex colours or a one-off gradient for
  an existing semantic state. Add a token when a new semantic state is real.
- Meet WCAG 2.1 AA contrast for text and controls. Do not encode status only by
  colour: combine it with text, icon shape, pattern, or numeric value. Verify
  palettes for common colour-vision differences, particularly hazard layers.
- Use the spacing, radius, shadow, and typography scales already present. Avoid
  inline `style` for layout or colour; an exception is a typed, data-driven
  visual value such as a deck.gl feature width, and it must be clamped.
- Design map panels from narrow viewport upward. Panels must scroll internally
  without hiding their close/action controls, retain a practical tap target,
  and not obscure essential map controls. Respect safe-area insets where they
  apply.
- Avoid cumulative layout shifts: reserve space for maps, images, charts, and
  async panels. Do not lock body scrolling globally unless the active route is
  genuinely map-first and dialogs/panels still behave correctly.

## Map interaction and render performance

- Keep pointer move, drag, zoom, and streaming updates off the React hot path.
  Debounce text searches, throttle map-derived display updates, and coalesce
  visual work with `requestAnimationFrame`. Do not render a full feature list
  or recreate a deck.gl layer for each pointer event.
- Render only the viewport/zoom-appropriate data. Use clustering, aggregation,
  tiles, virtualization, or progressive disclosure for large action networks;
  display count and filtering context so users know what is not shown.
- Tooltips are supplemental, not the only way to read a feature. They must not
  block map interaction, flicker during pointer movement, or trap focus. A
  selected feature opens an inspectable, keyboard-accessible details surface.
- Every map control must have a text label, selected/pressed state where
  applicable, and a sensible disabled state while its operation is unavailable.
  Avoid labels that depend on unexplained icons, acronyms, or technical source
  names.

## CSS and JSX hygiene

- Tailwind classes may be composed with the shared `cn` helper. Keep class
  lists readable and in a consistent order; factor repeated visual states into
  a component variant or semantic class.
- Keep global CSS limited to tokens, resets, external-library overrides,
  animations, and truly cross-route layout rules. Scope application-specific
  styles to a component or semantic class. Do not use `!important` except a
  documented override of an unavoidable third-party style.
- Write valid, lowercase HTML/CSS. Use two spaces in CSS, use `0` without a
  unit, prefer logical properties when directional layout is not intended, and
  avoid ID selectors for styling. Keep animation declarations paired with a
  reduced-motion rule when they are not purely decorative.
- JSX strings are user-facing copy. Use clear verbs and expected outcomes
  (`"Refresh fire detections"`, not `"Submit"`), and avoid unexplained
  severity labels, fake precision, or promises of a field outcome.

## UI review checklist

1. Can a keyboard and screen-reader user complete the same task without using
   the map canvas or colour alone?
2. Is the data state, source, time, unit, and uncertainty visible before a user
   acts on it?
3. Does a consequential action show scope, require confirmation, and preserve
   input on a recoverable error?
4. Does the panel work at a narrow viewport without hiding focus, controls, or
   essential map context?
5. Does the change avoid a high-frequency React render or unbounded feature
   list?
