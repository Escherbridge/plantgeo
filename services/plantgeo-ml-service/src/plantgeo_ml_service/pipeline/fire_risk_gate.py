"""The FR-5 publication gate: what a run may write where, and which strata it may score.

Layer L3. Split out of `fire_risk_daily.py` when that module passed the size ceiling; the gate is
the one part of the lane whose whole job is to REFUSE, and it is worth reading on its own. The
argument for fetching the backtest receipt rather than trusting the artifact's copy of its verdicts
lives on `verified_cleared_strata` below, and in `AGENTS-fire-risk.md` in this directory.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Final

from plantgeo_ml_service.pipeline.object_store import (
    SCRATCH_PREFIX_ROOT,
    ScratchPrefixError,
    scratch_rooted_store,
    sha256_of,
)

if TYPE_CHECKING:
    from plantgeo_ml_service.method.ml.fire_risk_model import FireRiskArtifact
    from plantgeo_ml_service.pipeline.object_store import ObjectStore

#: What a run with NO artifact records where an artifact digest would go. A DECLARED sentinel rather
#: than an empty string: two artifact-less scratch runs of the same day and seed must still land on
#: one identity, and an empty digest reads as "we forgot to fill this in".
NO_ARTIFACT_SENTINEL: Final = "no-artifact"


class FireRiskPublicationGateError(RuntimeError):
    """Raised when a run would write scores to a real prefix without the backtest receipt FR-5 demands."""


class FireRiskDailyError(RuntimeError):
    """Raised when a daily run cannot proceed: a bad scratch prefix, or a day past the source ceiling."""


def target_store(store: ObjectStore, dry_run_prefix: str | None) -> ObjectStore:
    """Return the store this run writes through, refusing a scratch prefix that is not scratch."""
    try:
        return scratch_rooted_store(store, dry_run_prefix)
    except ScratchPrefixError as error:
        raise FireRiskDailyError(str(error)) from error


def verified_cleared_strata(store: ObjectStore, artifact: FireRiskArtifact) -> tuple[str, ...]:
    """Load the artifact's backtest receipt, verify its bytes, and read the cleared strata FROM IT.

    The artifact's own `cleared_strata` list is a CLAIM about a document, and a claim is not the
    document: an artifact edited to widen that list would otherwise publish scores for strata no
    walk-forward fold ever measured. So the receipt is fetched, digested against the binding the
    artifact names, and its verdicts are what gate publication. The claim must agree or the artifact
    disagrees with its own evidence and is refused.
    """
    reference = artifact.backtest
    if reference is None:
        raise FireRiskPublicationGateError(
            "this artifact carries no backtest receipt, so no stratum has a measured out-of-sample lift; "
            f"FR-5 permits it only under a {SCRATCH_PREFIX_ROOT!r} prefix"
        )
    payload = store.read_object(reference.key)
    if payload is None:
        raise FireRiskPublicationGateError(
            f"the artifact names a backtest receipt at {reference.key!r} that this store does not hold; "
            "FR-5's gate is the receipt itself, not a reference to one"
        )
    digest = sha256_of(payload)
    if digest != reference.sha256:
        raise FireRiskPublicationGateError(
            f"the backtest receipt at {reference.key!r} digests to {digest}, not the {reference.sha256} the "
            "artifact binds; the object under that key is not the evidence this artifact was gated on"
        )
    cleared = _receipt_cleared_strata(payload, key=reference.key)
    if cleared != reference.cleared_strata:
        raise FireRiskPublicationGateError(
            f"the backtest receipt at {reference.key!r} cleared {cleared}, while the artifact claims "
            f"{reference.cleared_strata}; an artifact that disagrees with its own evidence is refused"
        )
    return cleared


def _receipt_cleared_strata(payload: bytes, *, key: str) -> tuple[str, ...]:
    """Read the verdict list out of a backtest receipt document, refusing a shape that has none."""
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise FireRiskPublicationGateError(f"the backtest receipt at {key!r} is not decodable JSON") from error
    cleared = document.get("cleared_strata") if isinstance(document, dict) else None
    if not isinstance(cleared, list) or not all(isinstance(name, str) for name in cleared):
        raise FireRiskPublicationGateError(
            f"the backtest receipt at {key!r} carries no `cleared_strata` list, so it gates nothing"
        )
    return tuple(cleared)


def artifact_sha(artifact: FireRiskArtifact | None) -> str:
    """Return the artifact digest every row names, or the declared sentinel for a run that has none."""
    return NO_ARTIFACT_SENTINEL if artifact is None else artifact.sha256


__all__ = [
    "NO_ARTIFACT_SENTINEL",
    "SCRATCH_PREFIX_ROOT",
    "FireRiskDailyError",
    "FireRiskPublicationGateError",
    "artifact_sha",
    "target_store",
    "verified_cleared_strata",
]
