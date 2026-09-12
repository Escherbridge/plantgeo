---
type: source-metadata-receipt
status: blocked
captured_on: 2026-09-12
---

# WTU and UBC bounded metadata refresh

## Outcome

Seven allowlisted metadata-only GETs completed successfully under the existing
eight-attempt, 30-second/request and ten-minute ceilings. Every body was capped
at 1 MiB. No specimen archive, image, RTF, script or iframe was requested; no
data-use agreement was accepted; no institution was contacted. Requested and
final URLs matched for every response, so this run observed no redirect hop.

The pre-acquisition verdict remains **blocked for both collections**. WTU still
lacks standalone exact-release EML, meta.xml/field mapping and immutable release
identity. UBC v16.43 retains exact CC0 metadata, but no captured institutional
statement binds public-coordinate withholding/generalization policy to that IPT
distribution. Because those gates are missing, the archive URLs were not probed
with GET or HEAD and the two-archive budget remains entirely unused.

## Captured HTTP evidence

The machine receipt is
[`pnw-admission-metadata-refresh-http-receipts-20260912.json`](../../../../.omc/research/pnw-admission-metadata-refresh-http-receipts-20260912.json);
the bounded capture recipe is
[`pnw-admission-metadata-refresh-20260912.py`](../../../../.omc/research/pnw-admission-metadata-refresh-20260912.py).
All retrieval times below are UTC.

| Name | Requested and final URL | Retrieved | Status / type | Bytes | Validators | SHA-256 |
| --- | --- | --- | --- | ---: | --- | --- |
| Portal inventory | `https://www.pnwherbaria.org/data/datasets.php` | `2026-09-12T04:51:36.505120+00:00` | 200 / `text/html; charset=UTF-8` | 65,330 | ETag absent; Last-Modified absent; Content-Length absent (chunked) | `61427faae1cbcb5d74e64c083666b53991242d6f65232b1d26ee792f10a7ee9a` |
| Portal usage policy | `https://www.pnwherbaria.org/data/datausagepolicy.php` | `2026-09-12T04:51:36.951149+00:00` | 200 / `text/html; charset=UTF-8` | 8,907 | ETag absent; Last-Modified absent; Content-Length absent (chunked) | `708d53ae2800fc51e079fe63bf8f268430de37a97cd54ae1a05c77c5fe804fc7` |
| WTU provider | `https://www.pnwherbaria.org/data/providermetadata.php?code=WTU` | `2026-09-12T04:51:37.167302+00:00` | 200 / `text/html; charset=UTF-8` | 21,090 | ETag absent; Last-Modified absent; Content-Length absent (chunked) | `ef15ec1c4b18ac32f43e0d5e117662ed6bdf0bc936d4d039efa5b5fbcf3e1102` |
| UBC provider | `https://www.pnwherbaria.org/data/providermetadata.php?code=UBC` | `2026-09-12T04:51:37.467356+00:00` | 200 / `text/html; charset=UTF-8` | 19,377 | ETag absent; Last-Modified absent; Content-Length absent (chunked) | `922b4750c7221d198129da5ff0b1ec5fe7039b73f794a64ec8457513510f9f08` |
| UBC IPT current | `https://data.canadensys.net/ipt/resource?r=ubc-vascular-specimens` | `2026-09-12T04:51:37.760317+00:00` | 200 / `text/html;charset=UTF-8` | 86,109 | ETag absent; Last-Modified absent; Content-Length absent (chunked) | `21e95a58eea8b31751a8964b22cf1b1136b782595f7c293de11ed9fc89610e23` |
| UBC IPT v16.43 | `https://data.canadensys.net/ipt/resource?r=ubc-vascular-specimens&v=16.43` | `2026-09-12T04:51:38.405201+00:00` | 200 / `text/html;charset=UTF-8` | 86,181 | ETag absent; Last-Modified absent; Content-Length absent (chunked) | `35b671ba0f41ceef6c6d7c86325ac36a03499770b5ba9f45be2ff934553dc9ba` |
| UBC EML v16.43 | `https://data.canadensys.net/ipt/eml.do?r=ubc-vascular-specimens&v=16.43` | `2026-09-12T04:51:38.877149+00:00` | 200 / `text/xml;charset=UTF-8` | 14,208 | ETag absent; Last-Modified `Tue, 01 Sep 2026 12:00:01 GMT`; Content-Length absent (chunked); disposition `filename="eml-ubc-vascular-specimens-v16.43.xml"` | `e73735e4eafcb1a233514948f72631ceced3dd22e30445d45f34e593f7293654` |

Server and Date headers are preserved verbatim in the machine receipt. HTTP
validators are transport facts, not biological release watermarks.

## Measured source facts

The current UBC IPT page still identifies v16.43 as the latest public release,
published `2026-09-01 12:00:36` with 192,948 core records and advertised 31 MB.
It still lists v16.42 at `2026-07-29 20:08:30` with 192,783 records. These are
publisher assertions, not measured archive completeness or immutable custody.
The versioned EML is byte-identical to September 11: package
`07fd0d79-4883-435f-bba1-58fef110cd13/v16.43`, publication date 2026-09-01,
CC0-1.0, UBC publisher/rightsholder, and SHA-256 shown above.

The portal inventory continues to say public datasets omit sensitive localities
and exclude records not available for public release. That statement governs the
portal distribution; it is not evidence that the broader institutional UBC IPT
applies the same suppression. The captured WTU provider page continues to label
vascular specimen facts CC0 separately from image terms, but exposes neither a
standalone release-bound EML/meta.xml nor a stable release identifier.

No actual Darwin Core field map, archive member list, record population, source
watermark, native identifier or schema fact was measured. Those facts reside in
unrequested archives. Advertising an archive link, size or record count does not
close the field-map, safety, completeness or identity gates.

## Exact blockers and next independent gate

- WTU: obtain release-bound EML/terms, complete-public-population and coordinate
  policy binding, immutable release identity plus an equivalent prior release,
  and approved custody/archive-control preflight.
- UBC: obtain an authoritative statement that the institutional IPT distribution
  suppresses or signals sensitive/withheld/generalized coordinates at a scope
  safe for intended redistribution, plus approved custody/archive-control preflight.
- Both: only after those gates pass may one-at-a-time quarantine capture measure
  archive/member hashes, `meta.xml`, schema, populations and native-ID stability.
  A two-release UBC comparison consumes both archive slots and defers WTU.

Independent verification is requested for the raw-body hashes, HTTP receipt,
metadata assertions, unchanged admission state and stop-before-acquisition
decision. This author receipt is not approval; `admitted_releases` remains empty.

Occurrence specimens remain spatial collection-event evidence only. They are not
nonspatial species profiles and do not prove growth, fuel, water/oil content,
suitability, current occupancy, abundance, absence or objective effects.
