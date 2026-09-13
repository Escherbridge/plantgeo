---
type: source-metadata-receipt
status: gate-still-open
captured_on: 2026-09-13
---

# UBC/Canadensys coordinate-withholding policy: public-metadata search exhausted

## Outcome

This receipt closes out the *research* side of one specific blocker —
"institutional public-coordinate/withholding policy applicability" for UBC
vascular — by establishing that it cannot be closed through more public
metadata. It does not admit anything. `admitted_releases` remains empty for
both collections.

Three additional bounded, allowlisted, redirect-refusing GETs (methodology
matching
[wtu-gbif-identity-20260912.md](wtu-gbif-identity-20260912.md)) checked every
place such a policy would plausibly be published:

| Source | URL | Result |
| --- | --- | --- |
| UBC EML `<methodStep>` (re-read in full) | already captured 2026-09-12 | `<methodStep><description><para></para></description></methodStep>` — literally empty. No collection, QC, or coordinate-handling methodology is documented at all. |
| Canadensys homepage | `https://www.canadensys.net/` | No policy/terms link; only `/about` and a contact email. |
| Canadensys about page | `http://www.canadensys.net/about/` | "explore, download and use them for free under an open license" — a licensing statement, not a sensitivity/withholding policy. No mention of sensitive species, coordinate generalization, or locality suppression anywhere on the page. |

Receipts:
[`pnw-admission-canadensys-policy2-20260913-http-receipts.json`](../../../../.omc/research/pnw-admission-canadensys-policy2-20260913-http-receipts.json),
[`pnw-admission-canadensys-about-20260913-http-receipts.json`](../../../../.omc/research/pnw-admission-canadensys-about-20260913-http-receipts.json).
Two same-host canonicalizing redirects were correctly refused by the
capture tool's fail-closed redirect handler
(`canadensys.net` → `data.canadensys.net/vascan/`,
`www.canadensys.net/about` → `.../about/`) and re-fetched at their exact
final URL rather than followed automatically.

## What this means for the gate

The [source-governance audit](source-governance-audit-20260912.md) already
named this gate; this pass confirms there is no third public document to
check. **Neither UBC's own EML, the institutional IPT distribution page, nor
the Canadensys network's own about page states any coordinate-withholding,
generalization, or sensitive-locality practice.** The portal's
`datausagepolicy.php` statement ("public exports omit sensitive localities")
governs only the `pnwherbaria.org` portal distribution, which is a different
object from the institutional Canadensys IPT release this track is
evaluating. Absence of a stated policy is not evidence that the archive is
safe to redistribute, nor evidence that it is unsafe — it is simply unproven,
and the pilot's own rules (see spec.md, "Reject ... any attempt to recover
withheld coordinates") mean the pilot cannot resolve this by inspecting an
unpermitted archive.

Two paths close this gate, and both require the parent/operator rather than
more research:
1. **Institutional response.** Send the (now-corrected, see
   [wtu-gbif-identity-correction-20260913.md](wtu-gbif-identity-correction-20260913.md))
   outreach draft and receive an explicit answer.
2. **Owner risk decision.** Accept per-record `informationWithheld`/
   `dataGeneralizations` Darwin Core fields, if present and populated in the
   archive itself, as sufficient evidence of correct suppression practice —
   checked *after* a permitted quarantine capture, not before it. This
   reorders which evidence gates the quarantine step, but does not skip a
   check; it would need explicit owner sign-off since it changes the pilot's
   stated pre-acquisition/post-capture split.

This receipt recommends option 2 be considered by the operator, since option
1 has an unbounded timeline outside this track's control, but does not by
itself elect that decision.
