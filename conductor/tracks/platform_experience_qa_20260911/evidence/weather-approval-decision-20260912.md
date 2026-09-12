---
type: approval-receipt
track: platform_experience_qa_20260911
recorded_on: 2026-09-12
status: approved_bounded_local_scope
---

# Coordinator approval: historical weather presentation

The historical Wind & Weather work is approved for the fixed local desktop
unavailable-state scope defined by
[the detailed weather approval receipt](weather-approval-20260912.md).
The approval is based on the owner implementation and focused receipts, the
root browser evidence, and the independent review recorded in that receipt.

The approved behavior includes the traditional report card with selected day,
source/product and SI units; an explicit retrying or unavailable state with no
spaced-square placeholder grid or fallback frame; refusal of stale responses;
sampled, aggregate and unmeasured support labels; idempotent recovery after a
late `style.load`; and clearing a ready report when the next response is
upstream-unavailable. The follow-up local presentation candidate integrated at
root `e54d091` also gives stronger wind labels placement priority while
retaining collision avoidance and states that rule in the legend.

This approval covers local presentation behavior only. It does not approve
populated-data release, forecast implementation, provider or model admission,
database or object-store mutation, data-writer execution, Railway, deployment
or push.

The platform QA track remains active for governed populated-data evidence,
dense wind-label collision behavior, precipitation hover, real selected-day
transitions, narrow-mobile and touch behavior, and the requested-day versus
served-day policy. The two forecast tracks remain planned and still require
source admission, immutable run publication, bounded readers, agent parity and
independent scientific, accessibility and data-contract acceptance.
