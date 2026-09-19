"""Provider sources: the typed contract a source satisfies, and the bindings that implement it.

The ONLY package in this service that performs HTTP. `method/` stays HTTP-free by the layer import
contract, so an estimator can never reach a provider on its own. See `AGENTS.md`.
"""

from __future__ import annotations
