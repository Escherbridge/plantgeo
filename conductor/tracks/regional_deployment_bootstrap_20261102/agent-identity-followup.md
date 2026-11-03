---
type: review
---
# Agent transport identity follow-up

Independent inspection after checkpoint `edd49302` found an incomplete downstream boundary. The web agent bridge in `src/lib/server/services/regional-evidence-tools.ts` calls the Python catalogue and execution endpoints through `fetchBoundedJson`; `routes/agent_tools.py` does not install the shared region identity middleware. Map transport checks therefore do not establish the identity of an agent response. The browser's tRPC check covers the web definition only.

Close both bridge calls with the shared bounded identity transport, and check identity before Python environmental-tool execution. Test matching definitions, contradictory definitions and missing required identity while retaining deliberate packaged legacy compatibility. The refusal must occur before tool invocation, not after evidence is returned.

Also preserve a typed sanitized source-admission refusal: `RegionAdmissionError` inherits `ValueError`, so the route's broad argument-error handler currently labels it `invalid_tool_arguments`. Missing/mismatched namespace and unadmitted support are deployment/data readiness conditions, not arguments the model can repair. Test these refusals without observation reads.

The current custom-storage gate still refuses access; this review does not establish an unsafe custom read. The shared generic evidence tools retain published-empty versus ungenerated states and requested versus served days. These findings concern the missing identity exchange and error classification, not a newly observed temporal conflation.

Status: implemented and independently reviewed in the inactive-bootstrap batch. Both bridge operations use shared identity transport; the actual blueprint installs middleware, and late admission failures return sanitized 409 `region_admission_refused` before argument handling. Full Python and frontend tests, with the documented typing follow-up, passed; see `bootstrap-evidence.md`. Authenticated live agent execution remains unverified, and custom admission remains blocked.
