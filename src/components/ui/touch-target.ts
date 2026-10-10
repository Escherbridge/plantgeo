/**
 * The 44px mobile tap-target floor, named once for the whole app.
 *
 * `dock-disclosure.ts` already called `max-sm:min-h-11` "the 44px mobile tap target every
 * control on this map holds to", and `MapManagerMobileLayout.test.tsx` asserted it -- but only
 * for the three sections the dock's *chrome* owns. The reports that chrome MOUNTS never got it:
 * on 2026-10-10 a grep for `max-sm:` across all 33 files of `src/components/panels/` returned
 * zero. So the layer tree held the floor while the soil, climate, water and fire readouts it
 * opens -- the surfaces a reader actually spends time in -- shipped 26px chips.
 *
 * Naming the strings here is what makes the floor reviewable instead of remembered. A grep for
 * `TOUCH_TARGET` now finds every control that has been considered; a control with neither the
 * constant nor the literal has not been.
 *
 * ## Why these are `max-sm:` and not unconditional
 *
 * A pointer resolves to one CSS pixel and a fingertip to about 44 of them, so the floor is a
 * property of the input device, not of the control. Raising every button to 44px on the desktop
 * would cost the 19rem dock roughly a third of its vertical density for readers who are using a
 * mouse. `max-sm` is the viewport proxy this codebase already chose for "phone" (it is what
 * `PANEL_SHELL` branches on to become a full-screen overlay), so the floor rides the same
 * breakpoint rather than introducing a second definition of mobile.
 *
 * `pointer: coarse` would be the honest query and is deliberately NOT used: a touchscreen laptop
 * matches it, and the dock's desktop column is the right layout there. One definition of mobile,
 * even an imperfect one, beats two that disagree.
 *
 * ## The width inversion, which is the trap in this file
 *
 * The dock is `w-[19rem]` (304px) on the desktop and `max-sm:w-full` on a phone -- and any
 * viewport from 320px up is wider than 304px, while `max-sm` reaches 639px. **A dock body
 * therefore has at least as much horizontal room on a phone as on a laptop, and usually more.**
 * (A 280px foldable cover screen is the one exception, and it is narrow enough that a single
 * column is wrong for a different reason.) Nothing inside `src/components/panels/` needs a `max-sm:grid-cols-1`; adding
 * one makes the phone layout worse than the desktop layout it was copied from. Column collapses
 * belong only where the container is the viewport itself -- the dashboard, the admin tables, the
 * editorial routes -- which is why they are not expressed here.
 */

/**
 * Any control laid out as a row: a button, a `role="switch"`, a tab, a select, a text input, a
 * list item that responds to a tap. Grows the box downward and leaves the type size alone.
 *
 * `min-h-*` rather than `h-*` on purpose -- a row whose label wraps to two lines must be allowed
 * to exceed the floor rather than clip, which is `panel-scroll.ts` rule 3 restated for content.
 *
 * **It is a no-op on a `display: inline` box.** CSS 2.1 10.5: `min-height` does not apply to
 * non-replaced inline elements, so pasting this onto a link sitting mid-sentence buys a class in
 * the diff and nothing on the screen. `<button>` (inline-block) and any flex or grid child
 * (blockified by CSS Display 3) are fine as they are; an inline `<a>` needs `inline-block` or
 * `inline-flex` first, and giving one that in the middle of a paragraph reflows the sentence
 * around it -- which is a layout decision, not a floor. Leave such links alone and say so.
 */
export const TOUCH_TARGET = "max-sm:min-h-11";

/**
 * An icon-only control, which has no label to give it width: a close button, a basemap swatch,
 * a caret. Needs both axes, since 44px of height around an 11px glyph is still an 11px-wide
 * target.
 *
 * **Not for a checkbox or a radio.** Their tap surface is the `<label>` wrapping them, since a
 * press anywhere on it toggles the control natively, so the floor belongs on the label -- which
 * already carries the text and padding that make it large. Putting it on the box paints a 44px
 * checkbox. The same holds for a radio row: floor the row, not the dot.
 */
export const TOUCH_TARGET_SQUARE = "max-sm:h-11 max-sm:w-11";

/**
 * 16px type on a phone for native text inputs, and the one place this pass raises a font size.
 *
 * Mobile Safari zooms the whole page when focus lands on an `input`, `select` or `textarea`
 * whose computed font-size is under 16px, and it does not zoom back out on blur. On a full-bleed
 * map that leaves the reader pinch-zoomed into a corner of the canvas with the dock off screen.
 * There is no CSS that declines the zoom; `maximum-scale=1` in the viewport meta suppresses it
 * only by taking pinch-zoom away from everyone, which is WCAG 2.2 SC 1.4.4 and not a trade this
 * app should make.
 *
 * So it is 16px on phones or it is a page zoom, and 16px is the smaller harm. This is scoped to
 * the FIELD, never to the labels, units or provenance lines beside it -- the owner's 2026-10-10
 * decision was to hold 10px granularity type as it is, and the exception here is about an
 * interaction defect rather than legibility. Reverting it is one constant.
 */
export const NO_IOS_FOCUS_ZOOM = "max-sm:text-base";
