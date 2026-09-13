---
type: source-admission-handoff
status: blocked
updated_on: 2026-09-13
---

# Corrected WTU draft, and the precise remaining gap for both collections

These are drafts only. No institution has been contacted, no agreement
signed, no terms accepted, no data requested externally. Sending either
draft is an operator decision this receipt does not make.

## Why the WTU draft in requests-and-handoff.md is wrong

[The September 11 draft](requests-and-handoff.md#wtu-draft) says "Your
provider page labels specimen facts CC0" and asks WTU to "confirm the
applicable terms." [wtu-gbif-identity-20260912.md](wtu-gbif-identity-20260912.md)
found the actual release-bound EML for both measured releases (v1.0, v1.1)
states **CC-BY 4.0**, and GBIF's own dataset registry record agrees. The old
draft's premise is measurably wrong for the distribution this track is
actually evaluating (`ipt.pnwherbaria.org`, GBIF UUID
`8310f570-f762-11e1-a439-00145eb45e9a`), not the older
`pnwherbaria.org/data` portal export it was written against. A message built
on a false premise would ask the wrong question and could draw an answer
that does not actually cover the real distribution's terms.

## Corrected WTU draft

Recipient role: WTU Herbarium collection/data manager. The
[GBIF dataset record](https://www.gbif.org/dataset/8310f570-f762-11e1-a439-00145eb45e9a)
and the [IPT resource page](https://ipt.pnwherbaria.org/resource?r=wtuvascular)
are the authoritative distribution; confirm the current recipient there
rather than through the older `pnwherbaria.org/data` portal contact.

Subject: WTU vascular DwC-A (ipt.pnwherbaria.org, v1.1): coordinate policy and archive custody

PlantGeo is evaluating the public WTU vascular specimen dataset (GBIF UUID
`8310f570-f762-11e1-a439-00145eb45e9a`, currently v1.1, published 2026-03-12)
for a data-only pilot. Proposed use is immutable source archiving,
normalization, public maps, downloads and documented-taxon summaries,
including possible commercial use. We exclude all images, media, restricted
records and any locality your institution withholds or generalizes.

We have inspected the release-bound EML for v1.1 and the prior v1.0 release,
both of which state CC-BY 4.0 attribution licensing. We have not downloaded
the archive, and this message does not accept those or any other terms.

Three things this pilot needs before it can proceed, which the EML does not
state: (1) whether this DwC-A distribution suppresses or generalizes
coordinates for sensitive taxa or locations, and if so, how that is signaled
in the exported data (e.g. `informationWithheld`/`dataGeneralizations`
fields, coordinate rounding, or record exclusion); (2) the attribution text
you require for CC-BY compliance at the redistribution scope described
above; (3) whether any additional written permission or signed agreement is
needed beyond the stated CC-BY terms for this reuse, and if so, the
agreement itself for our operator's separate review. We would also welcome,
though do not require for this message, confirmation that v1.0 and v1.1 are
equivalent complete exports with stable native occurrence identifiers, to
support a planned two-release identity comparison.

## Corrected framing for the UBC draft

[The existing UBC draft](requests-and-handoff.md#ubc-draft) already avoids
the CC0/CC-BY error (UBC's EML is genuinely CC0) and remains accurate as
written. [coordinate-policy-search-exhausted-20260913.md](coordinate-policy-search-exhausted-20260913.md)
adds one fact worth folding in before sending: neither UBC's EML nor the
Canadensys network's own about page states a coordinate-withholding policy
anywhere, so the draft's existing ask ("confirm whether this institutional
export suppresses sensitive records and coordinates") is not a formality —
it is the one gate this track's own research could not close by itself, and
no answer should be assumed either way pending a reply.

## Unchanged from the original handoff

The [operator and independent reviewer checklist](requests-and-handoff.md#operator-and-independent-reviewer-checklist)
and the two-archive, one-transfer-at-a-time budget in
[admission-decisions.json](admission-decisions.json) are unchanged. Neither
draft above authorizes sending, and sending either is a separate operator
decision from writing it.
