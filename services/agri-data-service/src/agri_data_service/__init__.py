"""Agri Data Service — regenerative agriculture data warehouse for PlantGeo."""

from agri_data_service.foundation import canonical_json, sha256_digest

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "canonical_json",
    "sha256_digest",
]

# The one guarded `arm_from_environment()` call (design §1.3, GL-1; see
# `foundation/observability/AGENTS.md` "Arming"). `arm_from_environment` itself fails open on every
# branch; this call site guards it again anyway, because nothing may prevent this package from
# importing.
try:
    from agri_data_service.foundation.observability.bootstrap import arm_from_environment

    arm_from_environment()
except Exception:  # importing this package must never fail because logging could not arm
    pass
