"""Neutral verdict on a decoded ArcGIS JSON envelope; callers map it (`_shared/AGENTS.md` §ArcGIS)."""

from __future__ import annotations

from enum import StrEnum


class ArcgisEnvelopeFault(StrEnum):
    """Why a decoded ArcGIS answer cannot be admitted as a complete population."""

    #: The body decoded to something other than a JSON object.
    NOT_AN_OBJECT = "not_an_object"
    #: The object carries an `"error"` key at all (key presence, not truthiness).
    SERVICE_ERROR = "service_error"
    #: `exceededTransferLimit` is truthy: the server clipped the answer.
    TRUNCATED = "truncated"


def arcgis_envelope_fault(parsed: object) -> ArcgisEnvelopeFault | None:
    """Return the first refusal a complete-population ArcGIS answer fails, or None when it may be admitted."""
    if not isinstance(parsed, dict):
        return ArcgisEnvelopeFault.NOT_AN_OBJECT
    if "error" in parsed:
        return ArcgisEnvelopeFault.SERVICE_ERROR
    if parsed.get("exceededTransferLimit"):
        return ArcgisEnvelopeFault.TRUNCATED
    return None


__all__ = ["ArcgisEnvelopeFault", "arcgis_envelope_fault"]
