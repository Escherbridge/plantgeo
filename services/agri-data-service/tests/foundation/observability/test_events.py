"""`events.py` is a name registry: every constant is a distinct, correctly-shaped string.

Unchanged from revision 1 (design record §6.1): this file only ever asserts shape and uniqueness,
never emission -- each event's emitter has its own test where it actually logs.
"""

from __future__ import annotations

import re

from agri_data_service.foundation.observability import events

_NEW_EVENT_NAME: re.Pattern[str] = re.compile(r"^plantgeo_[a-z0-9_]+$")

# Existing names this module deliberately keeps rather than renaming (design §1.1's
# "existing names are kept").
_KEPT_EXISTING_NAMES = frozenset({events.EVENT_REPAIR_AUTHORING_FAILED})


def _event_constants() -> dict[str, str]:
    return {name: value for name, value in vars(events).items() if name.startswith("EVENT_") and isinstance(value, str)}


def test_every_event_constant_is_a_non_empty_string() -> None:
    constants = _event_constants()
    assert constants, "expected at least one EVENT_* constant"
    for name, value in constants.items():
        assert isinstance(value, str), f"{name} must be a string"
        assert value, f"{name} must be non-empty"


def test_new_event_names_follow_the_component_noun_verb_convention() -> None:
    constants = _event_constants()
    for name, value in constants.items():
        if value in _KEPT_EXISTING_NAMES:
            continue
        assert _NEW_EVENT_NAME.match(value), f"{name}={value!r} does not match plantgeo_<component>_<noun>_<verb>"


def test_no_two_constants_share_a_value() -> None:
    constants = _event_constants()
    values = list(constants.values())
    assert len(values) == len(set(values)), "every EVENT_* constant must name a distinct event"


def test_no_declined_wave_o_names_are_present() -> None:
    """WQ-4 declined the GL-5 pool brake and `POOL_BULK_LANES`; neither name belongs here."""
    constants = _event_constants()
    values = set(constants.values())
    assert "plantgeo_job_executor_pool_saturated" not in values
    assert not any("pool_saturated" in value for value in values)
