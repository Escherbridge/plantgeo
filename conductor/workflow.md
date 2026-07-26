---
type: workflow
---

# Conductor workflow

Follow the repository `AGENTS.md` for coding, testing, security, and Git
requirements. This document governs only Conductor state.

1. Choose work from [`tracks.md`](./tracks.md), not a historical track file.
2. Read the track metadata, specification, plan, linked evidence, and the
   applicable runtime/data contract before changing code or data.
3. Record the smallest truthful status change in both the registry and track
   metadata. Do not turn an external dependency into an `active` task.
4. Keep evidence immutable and dated. Add a current-state note or successor
   track instead of editing historical findings into a new conclusion.
5. Apply changes in one bounded writer lane; obtain an independent review for
   governance, statistical, security, or release-boundary changes.
6. Run the project-integrated verification sweep once after the change batch,
   as required by `AGENTS.md`, and record only its actual result.

For a release, [`release-governance.md`](./release-governance.md) takes
precedence over every track. For data/forecast/ML work, an evaluation result is
not a publication or an intervention-effect claim.
