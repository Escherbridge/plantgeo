---
type: verification-evidence
---
# Existing dependency risk observed during release

The November 2 release build's npm audit warning was independently triaged read-only. `package.json` and `package-lock.json` are unchanged between the former runtime commit `edb2dfb` and data-fix commit `8119cc05`. This release neither introduces nor remediates these dependency versions.

| Scope | Critical | High | Moderate | Low | Package records |
| --- | ---: | ---: | ---: | ---: | ---: |
| Full audit | 8 | 11 | 29 | 1 | 49 |
| Production dependency tree (`--omit=dev`) | 5 | 7 | 5 | 1 | 18 |

Counts include propagated dependency effects. They are not counts of independent vulnerabilities or proven reachable exploits. A production dependency-tree record also does not establish inclusion in the final standalone image.

Direct production findings include Next 16.2.2, next-auth 4.24.13, the Auth Drizzle adapter 1.11.1, MapLibre 5.22.0, Drizzle 0.39.3, Zod 3.25.76 and BullMQ 5.73.0. PostCSS also remains in the production tree through Next.

Prioritize a separate dependency patch and reachability review:

- Next/Auth: middleware calls `getToken` without a catch on dashboard/onboarding routes, making the [malformed Bearer token exception advisory](https://github.com/advisories/GHSA-xmf8-cvqr-rfgj) relevant to verify. Conditional Google OAuth configuration also requires checking the [provider-binding advisory](https://github.com/advisories/GHSA-x445-f3h2-j279) when enabled. No exploit was attempted.
- MapLibre: review the [DOM sanitizer advisory](https://github.com/advisories/GHSA-jrc7-96c5-q579). Sampled popup templates independently escape text, so `setHTML` usage alone is not proof of exploitable XSS.
- Drizzle and Zod: assess user-influenced identifiers and request bounds against the [identifier escaping](https://github.com/advisories/GHSA-gpj5-g38j-94v9) and [oversized constrained collection](https://github.com/advisories/GHSA-mrhx-c7xc-m3w5) advisories.

All production records report a fix available, but npm proposes breaking migrations for MapLibre, Drizzle and Zod. Do not treat a blanket forced audit fix as a safe patch. Functional release gates do not establish security clearance.

Raw read-only audit evidence is retained locally in `.omc/research/dependency-audit-20261102.json` and `dependency-audit-all-20261102.json`. No dependency or production-setting changes were made during this assessment.
