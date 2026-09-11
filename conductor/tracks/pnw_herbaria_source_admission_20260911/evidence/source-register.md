---
type: source-register
captured_on: 2026-09-11
---

# Source register and custody limits

The evidence is dated September 11, 2026. Web captures retain source URLs, tool
references and crawl indicators; some are cached extracts. Raw captures were
saved before scrt filtering. Byte-level custody applies only to the four direct
HTML/XML captures listed in HTTP receipts, not to an occurrence archive.

| Source | Official URL | Evidence and limitation |
| --- | --- | --- |
| Portal inventory | [datasets](https://www.pnwherbaria.org/data/datasets.php) | Public export scope, format, count/size/update assertions; direct HTML captured. |
| WTU metadata | [WTU](https://www.pnwherbaria.org/data/providermetadata.php?code=WTU) | Exact vascular section, portal access point, collection counts/terms; direct HTML captured. |
| UBC metadata | [UBC](https://www.pnwherbaria.org/data/providermetadata.php?code=UBC) | Vascular terms and institutional access route; web extract. |
| Portal usage | [usage policy](https://www.pnwherbaria.org/data/datausagepolicy.php) | Basic facts, sensitivity, provider accuracy and attribution; web extract. |
| Portal sharing | [sharing policy](https://www.pnwherbaria.org/data/datasharingpolicy.php) | Provider governance; no PlantGeo agreement implied. |
| UBC institutional resource | [Canadensys IPT](https://data.canadensys.net/ipt/resource?r=ubc-vascular-specimens) | Direct HTML contains version table not fully exposed in web extract. |
| UBC v16.43 EML | [versioned EML](https://data.canadensys.net/ipt/eml.do?r=ubc-vascular-specimens&v=16.43) | Direct XML, 14,208 bytes, SHA in packet/receipt; web click initially failed with internal error. |
| WTU institutional route | [Burke Plants & Fungi](https://www.burkemuseum.org/collections-and-research/biology/plants-and-fungi) | Database route leads to CPNWH; no independent institutional export agreement proved. |
| UBC institutional site | [Beaty Herbarium](https://beatymuseum.ubc.ca/research-2/collections/herbarium/) | Direct web fetch returned browser-verification content; indexed extract is weaker evidence. |
| UBC collections site | [collections](https://collections.beatymuseum.ubc.ca/) | Collection portal exists; website software license is not specimen rights. |
| UBC physical loan policy | [collection policies](https://beatymuseum.ubc.ca/research-2/loan-policies/) | Independent researcher found specimen/artifact agreements, not a digital CC0 restriction. |
| DwC standard | [Darwin Core terms](https://dwc.tdwg.org/terms/) | Candidate field semantics; not proof of actual archive mappings/population. |
| EML standard | [dataset schema](https://eml.ecoinformatics.org/schema/eml-dataset_xsd) | Metadata structure reference; no claim of complete XSD validation in this pass. |

## Retained captures

All linked files are bounded research evidence, not runtime artifacts:

- [Initial portal capture](../../../../.omc/research/pnw-admission-portal-20260911.md)
- [Institutional policy search](../../../../.omc/research/pnw-admission-rights-20260911.md)
- [Portal policy and UBC detail](../../../../.omc/research/pnw-admission-policy-detail-20260911.md)
- [Institutional and IPT page extracts](../../../../.omc/research/pnw-admission-institutional-20260911.md)
- [EML attempt and standards search](../../../../.omc/research/pnw-admission-eml-standards-20260911.md)
- [Standard term detail](../../../../.omc/research/pnw-admission-standard-detail-20260911.md)
- [Independent institutional-policy findings/captures](../../../../.omc/research/pnw-policy-independent-20260911.md)
- [Direct metadata capture recipe](../../../../.omc/research/pnw-admission-fetch-20260911.py)
- [HTML HTTP receipts](../../../../.omc/research/pnw-admission-http-receipts-20260911.json)
- [EML HTTP receipt](../../../../.omc/research/pnw-admission-eml-http-receipt-20260911.json)
- [WTU provider HTML](../../../../.omc/research/pnw-wtu-provider-20260911.html)
- [Portal inventory HTML](../../../../.omc/research/pnw-portal-inventory-20260911.html)
- [UBC IPT HTML](../../../../.omc/research/pnw-ubc-ipt-20260911.html)
- [UBC v16.43 EML](../../../../.omc/research/pnw-ubc-eml-16.43-20260911.xml)

Search captures also contain irrelevant results; they are not endorsed sources.
No Culture Department, physical loan, image, clinical, other-herbarium or
destructive-sampling agreement was used to decide vascular data rights.

The direct recipe allowed three public HTML pages and one later publisher-linked
EML, each capped at 1 MiB and 25 seconds/request, with no retry. Four successful
requests captured 186,764 bytes total. An earlier sandbox attempt failed before
any network body; it did not acquire data. HTTP receipts retain content headers,
status, final URL and SHA; ephemeral Set-Cookie values were deliberately removed.
The initial and final recorded URLs are metadata-only; no archive/image, HEAD,
script or iframe request was intentionally issued. The recipe used urllib's
default redirects and did not retain intermediate redirect history; the receipt
cannot independently attest every intermediate hop. The future archive protocol
requires per-hop allowlist enforcement. Web-tool page retrieval does not provide origin HTTP headers
or byte identity, so its extracts are separately identified.

[Final verification receipt](verification.json) records evidence hash and link checks.
The [verification recipe](../../../../.omc/research/pnw-admission-verify-20260911.py)
checks authored documentation while preserving raw source bytes verbatim.
The [capture attributes](../../../../.omc/research/.gitattributes) disable Git text
normalization for only the four direct HTML/XML captures so their hashes survive
commit and checkout. The first verification attempt found one trailing blank
line, corrected before the passing sweep; staging also exposed the need for
these byte-preservation attributes.
