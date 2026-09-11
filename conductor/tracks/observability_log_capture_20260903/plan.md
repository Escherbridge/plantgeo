---
type: track-plan
slug: observability_log_capture_20260903
status: blocked
---

# Observability capture — next gate

The [September 3 charter](spec.md) defines the W1–W7 design and records six
unresolved decisions after D1's historical ingress check. No completed
implementation or durable-capture acceptance packet is recorded for this track.

- [ ] Record D2–D7: bucket provider, operator authentication, bounded tail cap,
  healthy-tick keepalive, retention and platform-tap scope.
- [ ] Refresh the charter's current-code inventory, private-ingress evidence and
  executor ownership before authoring; historical line numbers are not a current
  security assessment.
- [ ] Freeze the implementation partitions and execute the charter's structured
  logging, separate-bucket receipts, bounded child capture and platform backstop.
  Verify that bounded capture cannot stall heartbeat/fence maintenance.
- [ ] Gate the operator panel with the selected authentication mechanism before
  exposing it, and prove redaction, size bounds, failure behavior and retention.
- [ ] Complete independent review and one final integrated sweep after the full
  change batch, then record exact deployed capture/query evidence.
