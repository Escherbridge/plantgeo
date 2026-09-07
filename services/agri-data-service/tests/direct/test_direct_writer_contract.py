"""All eleven `pipeline/direct` writers, read as ONE table: outcome words, CLI surface, failure policy.

Sibling to `test_direct_package_registration.py`, which polices whether a writer is WIRED. This one
polices whether it is UNIFORM -- and, where it is deliberately not, whether it says so.

The writers were built over months by different passes, so they drifted: nine outcome spellings were
observable in production logs with no enumerable set behind them; `--bbox` reached four of the seven
writers whose upstream is bbox-bounded before the fifth crashed on a negative longitude; and two
answers to an unset `INGEST_BBOX` looked like a contradiction while actually being two correct
answers to two different lane shapes. `pipeline/direct/__init__.py` is where that is now declared,
each writer's `WRITER_CONTRACT` is where it is claimed, and this file is where the claim is checked
against the code.

WHAT THIS FILE WILL NOT DO IS FLATTEN A REAL DIFFERENCE. Every assertion below is either "this is
uniform" or "this differs AND the writer says why"; none is "this must become the same". The
negative controls at the bottom exist to fail if a genuine divergence is quietly normalised away,
which is the failure mode a uniformity pass has and a drift pass does not.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path
from typing import TYPE_CHECKING, Final, get_args

import pytest

from agri_data_service.pipeline.direct import (
    CONTRACT_FLAGS,
    DIRECT_TURN_OUTCOMES,
    KNOWN_SYNONYMS,
    LANE_DAY_OUTCOMES,
    NATURE_FLAGS,
    NOT_BBOX_BOUNDED,
    REFUSE_UNCONFIGURED_BBOX,
    REFUSE_WHOLE_RELEASE,
    SKIP_AND_COUNT,
    SKIP_TURN_ON_UNCONFIGURED_BBOX,
    USAGE_ERROR_ON_UNCONFIGURED_BBOX,
)
from agri_data_service.pipeline.direct.burn_severity.forward import (
    parse_args as parse_burn_severity_args,
)
from agri_data_service.pipeline.direct.burn_severity.forward import (
    run_burn_severity_forward,
)
from agri_data_service.pipeline.direct.fire_detections import _parse_args
from agri_data_service.pipeline.direct.watersheds.forward import (
    WatershedsForwardConfig,
    run_watersheds_forward,
)
from agri_data_service.pipeline.parquet.gap_fill import LaneDayOutcome

if TYPE_CHECKING:
    import argparse
    from types import ModuleType

    from agri_data_service.pipeline.direct import DirectWriterContract

_SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src" / "agri_data_service"
DIRECT_ROOT = _SOURCE_ROOT / "pipeline" / "direct"

#: Package (or flat module) name -> the module that owns its `parser()` and `WRITER_CONTRACT`.
#: DEFAULT-DENY: `test_every_direct_writer_is_in_this_table` fails if `pipeline/direct` grows a
#: writer nobody added here, so a twelfth writer is policed the day it lands rather than the day
#: somebody remembers. `water_gauges` is deliberately absent and its absence is asserted, not assumed
#: -- see `NON_WRITER_MODULES`.
WRITER_MODULES: Final[dict[str, str]] = {
    "burn_severity": "agri_data_service.pipeline.direct.burn_severity.forward",
    "climate": "agri_data_service.pipeline.direct.climate.forward",
    "drought": "agri_data_service.pipeline.direct.drought.forward",
    "evacuation_zones": "agri_data_service.pipeline.direct.evacuation_zones.forward",
    "fire_detections": "agri_data_service.pipeline.direct.fire_detections",
    "fire_perimeters": "agri_data_service.pipeline.direct.fire_perimeters.forward",
    "sensors": "agri_data_service.pipeline.direct.sensors.forward",
    "soil": "agri_data_service.pipeline.direct.soil.forward",
    "vegetation": "agri_data_service.pipeline.direct.vegetation.forward",
    "watersheds": "agri_data_service.pipeline.direct.watersheds.forward",
    "weather_observations": "agri_data_service.pipeline.direct.weather_observations.forward",
}

#: Modules under `pipeline/direct/` that are NOT forward writers and therefore owe no contract. One
#: entry, with its reason, because "eleven writers" is a claim this file should be able to prove
#: rather than a number it inherits.
NON_WRITER_MODULES: Final[dict[str, str]] = {
    "water_gauges": "a TRANSFORMER, not a writer: it holds `publisher_named_day`, "
    "`tables_by_publisher_day` and `merge_water_gauges_day`, which the water-gauges gap-fill lane "
    "calls. It has no `parser()`, no turn, and no bounded run to report an outcome for, so every "
    "field of a DirectWriterContract would be vacuous.",
}

#: The seven writers whose upstream really is bounded by `INGEST_BBOX`. Derived from each writer's
#: own declared `unconfigured_bbox` policy rather than hard-coded, so the list cannot disagree with
#: the declarations -- and `test_bbox_flag_presence_follows_the_declared_bbox_policy` is what pins
#: the two together.
BBOX_POLICIES_REQUIRING_THE_FLAG: Final[frozenset[str]] = frozenset(
    {REFUSE_UNCONFIGURED_BBOX, SKIP_TURN_ON_UNCONFIGURED_BBOX, USAGE_ERROR_ON_UNCONFIGURED_BBOX}
)

#: A real western-North-America envelope, chosen because its FIRST ORDINATE IS NEGATIVE. That is not
#: incidental: argparse reads a leading `-` as the start of another flag, so `--bbox -125,...` dies
#: with "argument --bbox: expected one argument" unless the writer routes argv through
#: `ingest/mtbs.py::inline_bbox_value` first. Every bbox this warehouse is configured with starts
#: with a negative longitude, so a writer missing the rewrite is not occasionally broken, it is
#: entirely unusable from the command line.
NEGATIVE_LEADING_BBOX: Final = "-125.0,42.0,-116.0,49.0"


def _module(package: str) -> ModuleType:
    return importlib.import_module(WRITER_MODULES[package])


def _contract(package: str) -> DirectWriterContract:
    contract: DirectWriterContract = _module(package).WRITER_CONTRACT
    return contract


def _flags(package: str) -> frozenset[str]:
    """Every long option one writer's `parser()` actually exposes, read off the real parser."""
    built: argparse.ArgumentParser = _module(package).parser()
    return frozenset(option for action in built._actions for option in action.option_strings)


def _literal_strings(node: ast.expr, namespace: dict[str, object]) -> set[str]:
    """Resolve one AST expression to the string(s) it can evaluate to, or nothing when it cannot.

    Handles the three shapes a writer actually uses: a bare literal, a conditional between two
    literals (`"idempotent_noop" if not backlog else "published"`), and a module-level constant by
    name (`BURN_SEVERITY_TIME_BUDGET_OUTCOME`). Anything else -- a local variable carrying a
    `fill_one_lane_day` result, an attribute, an f-string -- resolves to nothing and is skipped,
    which is why the assertion below runs in ONE direction only.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, ast.IfExp):
        return _literal_strings(node.body, namespace) | _literal_strings(node.orelse, namespace)
    if isinstance(node, ast.Name):
        resolved = namespace.get(node.id)
        return {resolved} if isinstance(resolved, str) else set()
    return set()


def _emitted_outcomes(package: str) -> dict[str, set[str]]:
    """Every outcome word one writer's WHOLE PACKAGE can emit as a literal, keyed by word.

    Scans the package's source rather than the contract, so the contract cannot certify itself. Both
    spellings a writer uses are collected: the `outcome=` keyword argument and the `"outcome":` dict
    entry that lands in the emitted JSON. `vegetation/backfill.py` is a real reason to walk the whole
    package rather than only `forward.py` -- it emits words `forward.py` never does.
    """
    namespace = vars(_module(package))
    target = DIRECT_ROOT / package
    files = sorted(target.rglob("*.py")) if target.is_dir() else [DIRECT_ROOT / f"{package}.py"]
    found: dict[str, set[str]] = {}
    for path in files:
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.keyword) and node.arg == "outcome":
                for word in _literal_strings(node.value, namespace):
                    found.setdefault(word, set()).add(path.name)
            elif isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values, strict=True):
                    if isinstance(key, ast.Constant) and key.value == "outcome":
                        for word in _literal_strings(value, namespace):
                            found.setdefault(word, set()).add(path.name)
    return found


PACKAGES: Final[tuple[str, ...]] = tuple(WRITER_MODULES)


# ---------------------------------------------------------------------------------------------
# The table is complete
# ---------------------------------------------------------------------------------------------


def test_every_direct_writer_is_in_this_table() -> None:
    """A writer nothing reads as part of the table is a writer free to drift back out of it."""
    on_disk = {path.name for path in DIRECT_ROOT.iterdir() if path.is_dir() and (path / "__init__.py").is_file()}
    on_disk |= {path.stem for path in DIRECT_ROOT.glob("*.py") if path.stem != "__init__"}
    accounted = set(WRITER_MODULES) | set(NON_WRITER_MODULES)
    unaccounted = sorted(on_disk - accounted)
    assert not unaccounted, (
        f"pipeline/direct module(s) {unaccounted} are neither in WRITER_MODULES nor excused in "
        "NON_WRITER_MODULES. Add the writer's module here so its contract is checked, or record why "
        "it owes no contract."
    )
    stale = sorted(accounted - on_disk)
    assert not stale, f"this table names module(s) {stale} that no longer exist"


def test_the_one_non_writer_module_really_has_no_parser() -> None:
    """The excuse in NON_WRITER_MODULES is checked, not taken on trust.

    Without this, `NON_WRITER_MODULES` would be a way to silence the table by asserting a module is
    not a writer -- which is exactly the vacuous exemption the rest of this file is built to refuse.
    """
    for package, reason in NON_WRITER_MODULES.items():
        assert reason.strip(), package
        module = importlib.import_module(f"agri_data_service.pipeline.direct.{package}")
        assert not hasattr(module, "parser"), (
            f"{package} is excused from the writer table but exposes a `parser()`; if it grew a CLI "
            "it grew a turn, and it now owes a WRITER_CONTRACT like its ten siblings."
        )


@pytest.mark.parametrize("package", PACKAGES)
def test_every_writer_declares_a_contract_and_a_parser(package: str) -> None:
    """The two things the whole table is read through, present on every writer with no exception."""
    module = _module(package)
    assert hasattr(module, "WRITER_CONTRACT"), f"{package} declares no WRITER_CONTRACT"
    assert callable(getattr(module, "parser", None)), (
        f"{package} exposes no `parser()` factory, so nothing outside it can enumerate its CLI "
        "surface -- and a knob nobody can enumerate is a knob that drifts unobserved."
    )


# ---------------------------------------------------------------------------------------------
# Shared outcome vocabulary
# ---------------------------------------------------------------------------------------------


def test_lane_day_outcomes_match_the_shared_literal() -> None:
    """`LANE_DAY_OUTCOMES` restates `gap_fill.LaneDayOutcome`; this is the anti-drift proof.

    It is a copy rather than an import because `gap_fill` imports `lane_registry`, which imports
    writer packages, which import `pipeline/direct/__init__.py` -- so importing it back would close a
    cycle. `pipeline/lanes/__init__.py` documents the identical restatement for the same reason. A
    restatement with no test behind it is just a second place to be wrong.
    """
    assert frozenset(get_args(LaneDayOutcome)) == LANE_DAY_OUTCOMES


@pytest.mark.parametrize("package", PACKAGES)
def test_every_declared_outcome_is_in_the_shared_vocabulary(package: str) -> None:
    """One monitor has to read all eleven writers, and it can only do that against an enumerable set."""
    undeclared = sorted(_contract(package).turn_outcomes - DIRECT_TURN_OUTCOMES)
    assert not undeclared, (
        f"{package} declares outcome(s) {undeclared} that pipeline/direct/__init__.py does not "
        "define. Add the word there with what it means and how it differs from its nearest "
        "neighbour, or use the existing word that already means it."
    )


@pytest.mark.parametrize("package", PACKAGES)
def test_no_writer_emits_an_outcome_it_did_not_declare(package: str) -> None:
    """Read the source, not the contract: otherwise the declaration would be certifying itself.

    ONE DIRECTION ONLY. A word found in the source must be declared; a word declared but not found is
    NOT an error, because the five `LaneDayOutcome` words reach the report through a local variable
    carrying `fill_one_lane_day`'s return value and no literal scan can see them.
    """
    declared = _contract(package).turn_outcomes
    emitted = _emitted_outcomes(package)
    undeclared = {word: sorted(files) for word, files in emitted.items() if word not in declared}
    assert not undeclared, (
        f"{package} emits outcome word(s) its WRITER_CONTRACT does not declare: {undeclared}. Add "
        "them to `turn_outcomes` (and to DIRECT_TURN_OUTCOMES if genuinely new), or stop emitting them."
    )


def test_every_synonym_pair_names_two_words_that_are_both_really_in_use() -> None:
    """`KNOWN_SYNONYMS` records a spelling collision; a stale entry would hide that it was resolved.

    Both halves must still be reachable: the direct-writer spelling from some writer's declared
    outcomes, and the warehouse spelling from `LaneDayOutcome`. When a rename finally lands, this
    fails and the entry is deleted in the same push -- which is the point.
    """
    all_declared: set[str] = set()
    for package in PACKAGES:
        all_declared |= _contract(package).turn_outcomes
    for direct_spelling, warehouse_spelling in KNOWN_SYNONYMS.items():
        assert direct_spelling in all_declared, (
            f"KNOWN_SYNONYMS still claims {direct_spelling!r} is in use, but no writer declares it. "
            "If the rename landed, delete the entry."
        )
        assert warehouse_spelling in LANE_DAY_OUTCOMES, (
            f"KNOWN_SYNONYMS maps {direct_spelling!r} onto {warehouse_spelling!r}, which is not a "
            "LaneDayOutcome word, so the pair does not name one state in two spellings."
        )


# ---------------------------------------------------------------------------------------------
# CLI flag parity
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("package", PACKAGES)
def test_every_writer_exposes_the_required_flags(package: str) -> None:
    """`--max-days` bounds a turn and `--run-id` correlates it; neither has an exemption available."""
    missing = sorted({"--max-days", "--run-id"} - _flags(package))
    assert not missing, (
        f"{package} exposes neither of, or not all of, the flags every direct writer must have: "
        f"missing {missing}. There is no exemption for these -- a turn nobody can bound or correlate "
        "is not operable by the same runbook as its ten siblings."
    )


@pytest.mark.parametrize("package", PACKAGES)
def test_every_nature_flag_is_exposed_or_excused_with_a_reason(package: str) -> None:
    """Default-deny on the CLI surface: a knob is present, or its absence is argued in prose."""
    contract = _contract(package)
    unexplained = sorted(NATURE_FLAGS - _flags(package) - set(contract.flags_absent_on_purpose))
    assert not unexplained, (
        f"{package} exposes neither flag(s) {unexplained} nor an entry for them in "
        "WRITER_CONTRACT.flags_absent_on_purpose. Expose the knob, or say in one sentence what this "
        "lane's nature is that makes it meaningless here."
    )


@pytest.mark.parametrize("package", PACKAGES)
def test_a_flag_declared_absent_on_purpose_is_genuinely_absent(package: str) -> None:
    """THE NON-VACUITY GUARD. Without it, excusing every flag would pass the test above trivially.

    An entry that names a flag the parser really does exposes is worse than no entry: it reads as a
    considered decision, and the reason beside it argues for behaviour the code does not have.
    """
    contract = _contract(package)
    exposed = _flags(package)
    contradicted = sorted(set(contract.flags_absent_on_purpose) & exposed)
    assert not contradicted, (
        f"{package} declares flag(s) {contradicted} absent on purpose while `parser()` exposes them. "
        "Either the flag was added and the excuse is now stale, or the excuse names the wrong flag."
    )
    # A smoke test for "n/a", not a quality bar: the reasons are prose and no test can grade them,
    # but a one-word entry is the observable shape of an exemption nobody thought about.
    minimum_words = 8
    for flag, reason in contract.flags_absent_on_purpose.items():
        assert flag in CONTRACT_FLAGS, f"{package} excuses {flag!r}, which no writer is expected to have"
        assert len(reason.split()) >= minimum_words, (
            f"{package}'s reason for omitting {flag} is too short to be a reason: {reason!r}"
        )


@pytest.mark.parametrize("package", PACKAGES)
def test_bbox_flag_presence_follows_the_declared_bbox_policy(package: str) -> None:
    """The CLI surface and the unset-bbox policy are two independent declarations; pin them together.

    A writer that answers an unset `INGEST_BBOX` at all is a writer with a bbox, so it must let an
    operator override it. A writer declared `not_bbox_bounded` -- a pinned lattice or a national
    release -- must NOT take one, because a bbox there would not narrow a query, it would silently
    change the SUPPORT the day is written against.
    """
    contract = _contract(package)
    exposes = "--bbox" in _flags(package)
    if contract.unconfigured_bbox in BBOX_POLICIES_REQUIRING_THE_FLAG:
        assert exposes, (
            f"{package} declares the unset-bbox policy {contract.unconfigured_bbox!r}, so its upstream "
            "IS bbox-bounded, but `parser()` exposes no `--bbox` for an operator to override "
            "INGEST_BBOX with."
        )
    else:
        assert not exposes, (
            f"{package} declares `not_bbox_bounded` but exposes `--bbox`. One of the two is wrong: a "
            "lattice or national lane taking a bbox would change its support, not its query."
        )


# ---------------------------------------------------------------------------------------------
# Behaviour pinned by this pass
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("package", PACKAGES)
def test_every_bbox_writer_survives_a_negative_leading_bbox(package: str) -> None:
    """REGRESSION PIN. `weather_observations` and `fire_detections` both died on this until 2026-09-07.

    Both accepted `--bbox` and neither routed argv through `inline_bbox_value`, so the documented
    operator command failed with "argument --bbox: expected one argument" for every real envelope --
    the same class of crash that adding `--bbox` to four writers was meant to end. Parametrised over
    ALL eleven so a writer that GAINS `--bbox` later is covered the day it does, with nothing to
    remember to add here.
    """
    if "--bbox" not in _flags(package):
        pytest.skip(f"{package} declares itself not bbox-bounded; see its WRITER_CONTRACT")
    module = _module(package)
    reader = getattr(module, "parse_args", None) or module._parse_args
    parsed = reader(["--bbox", NEGATIVE_LEADING_BBOX])
    resolved = getattr(parsed, "bbox", None)
    assert resolved is not None
    assert resolved.startswith("-125"), (
        f"{package} did not preserve a negative-leading bbox through argv parsing: got {resolved!r}"
    )


def test_fire_detections_accepts_an_operator_supplied_run_id() -> None:
    """PINS THE ADDED FLAG. Ten writers let an operator pin the run id; this one only generated its own.

    A generated-only run id means a turn's records cannot be correlated with the job that launched
    it, which is the whole reason the other ten take the flag. The default is unchanged: omitting it
    still generates one, so nothing that already runs this writer changes.
    """

    pinned = _parse_args(["--bbox", NEGATIVE_LEADING_BBOX, "--run-id", "operator-pinned"])
    assert pinned.run_id == "operator-pinned"
    generated = _parse_args(["--bbox", NEGATIVE_LEADING_BBOX])
    assert generated.run_id is None, "omitting --run-id must keep the generated default, not pin an empty one"


async def test_burn_severity_names_its_unset_bbox_skip_in_a_declared_word(monkeypatch: pytest.MonkeyPatch) -> None:
    """PINS THE ADDED KEY. This no-op reported only a `detail` sentence and no `outcome` at all.

    A monitor reading eleven writers had to string-match one writer's prose to learn a state that its
    sibling `evacuation_zones` had been reporting as a word since it was written. Returns before any
    socket or object-store call: the bbox check is the first thing past config validation.
    """

    monkeypatch.delenv("INGEST_BBOX", raising=False)
    # Through `parse_args` rather than the dataclass: this config declares no defaults for its six
    # budget fields, and hand-picking six numbers here would pin values the CLI is free to change.
    report = await run_burn_severity_forward(parse_burn_severity_args(["--run-id", "contract-probe"]))
    assert report["outcome"] == "bbox_unconfigured"
    assert report["outcome"] in _contract("burn_severity").turn_outcomes
    assert report["days_published"] == 0


async def test_watersheds_names_its_unset_bbox_skip_in_a_declared_word(monkeypatch: pytest.MonkeyPatch) -> None:
    """PINS THE ADDED KEY, and pins that the richer `state` survives beside it.

    `state` is a `StaticLaneState` and answers where the lane stands against its source watermark;
    `outcome` answers what the turn did. Both are kept -- the state is strictly more informative and
    the word is what generalises across the other ten writers.
    """

    monkeypatch.delenv("INGEST_BBOX", raising=False)
    report = await run_watersheds_forward(WatershedsForwardConfig(bbox=None, run_id="contract-probe"))
    assert report["outcome"] == "bbox_unconfigured"
    assert report["outcome"] in _contract("watersheds").turn_outcomes
    assert report["state"] == "watermark_unread", "the static-lane state must survive beside the shared word"
    assert report["published"] is False


@pytest.mark.parametrize("package", PACKAGES)
def test_every_writer_cites_a_basis_that_is_its_own(package: str) -> None:
    """A basis that could be pasted onto any other writer has stopped saying anything.

    Cheap proxy, deliberately: the reasons are prose and no test can grade them. What it CAN refuse
    is two writers shipping the identical sentence, which is the observable shape of a rationale
    copied from a sibling instead of thought about.
    """
    basis = _contract(package).policy_basis
    minimum_words = 20
    assert len(basis.split()) >= minimum_words, f"{package}'s policy_basis cites nothing: {basis!r}"
    twins = [other for other in PACKAGES if other != package and _contract(other).policy_basis == basis]
    assert not twins, f"{package} and {twins} ship an identical policy_basis; at least one of them was copied"


# ---------------------------------------------------------------------------------------------
# Negative controls: the differences that are REAL must survive this file
# ---------------------------------------------------------------------------------------------


def test_the_unset_bbox_split_between_fire_perimeters_and_evacuation_zones_still_exists() -> None:
    """NEGATIVE CONTROL. Two `static_lookup` lanes, same shape, deliberately opposite answers.

    `fire_perimeters` RAISES because its coverage has ONE bound: skipping would leave the previously
    published version serving under a coverage claim this turn never re-proved, and publishing over
    an unstated extent would stamp a version whose coverage nobody can cite.
    `evacuation_zones` SKIPS because its coverage is bounded TWICE -- Oregon's own statewide feed AND
    `INGEST_BBOX` -- so an unset envelope would WIDEN the query past the published coverage contract,
    and skipping leaves a version serving under a claim it already proved.

    If a later pass "unifies" these, this test fails and the two paragraphs above are why it should.
    """
    assert _contract("fire_perimeters").unconfigured_bbox == REFUSE_UNCONFIGURED_BBOX
    assert _contract("evacuation_zones").unconfigured_bbox == SKIP_TURN_ON_UNCONFIGURED_BBOX


def test_fire_perimeters_and_watersheds_agree_on_both_defect_axes() -> None:
    """NEGATIVE CONTROL, INVERTED: the pair that LOOKS divergent must keep testing identical.

    "fire-perimeters refuses a whole snapshot while watersheds publishes beside `rejected_basins: 0`"
    is a comparison ACROSS the defect axis, not along it. Both count an identity defect and publish
    (`fire_perimeters/rows.py:242`, `watersheds/source.py::_accept`); both refuse the whole
    population for a geometry that converts to empty (`fire_perimeters/support.py:75`,
    `watersheds/support.py:130`). What differed was which defect their live data contained.

    If this ever fails, one of the two lanes really did change policy and the "they were always the
    same" reading in `pipeline/direct/__init__.py` needs rewriting rather than repeating.
    """
    perimeters, watersheds = _contract("fire_perimeters"), _contract("watersheds")
    assert perimeters.identity_defect == watersheds.identity_defect == SKIP_AND_COUNT
    assert perimeters.geometry_defect == watersheds.geometry_defect == REFUSE_WHOLE_RELEASE


def test_fire_detections_is_still_the_lone_identity_refuser_and_says_so() -> None:
    """NEGATIVE CONTROL. The one genuine outlier on the identity axis, left alone on purpose.

    Its three record-bearing peers count an unkeyable record and publish; this writer refuses the
    whole day. That may well be right -- a FIRMS day is a complete constellation response whose row
    count is itself the evidence -- but it is an owner decision, not a uniformity edit, so this pass
    declared it instead of changing it. The assertion is two-sided: the outlier must still BE an
    outlier, and its own declaration must still name the tension, so nobody flips it without reading
    the argument first.
    """
    counters = {package for package in PACKAGES if _contract(package).identity_defect == SKIP_AND_COUNT}
    assert counters == {"fire_perimeters", "sensors", "watersheds"}, (
        "the set of writers that COUNT an identity defect changed; if a writer joined or left, "
        "pipeline/direct/__init__.py's 'the one real outlier' paragraph is now wrong"
    )
    contract = _contract("fire_detections")
    assert contract.identity_defect == REFUSE_WHOLE_RELEASE
    assert contract.unconfigured_bbox == USAGE_ERROR_ON_UNCONFIGURED_BBOX, (
        "fire-detections is also the only writer answering an unset INGEST_BBOX with argparse.error; "
        "that third answer is declared, and losing it silently would hide a real difference"
    )


def test_the_four_lattice_and_national_lanes_still_refuse_a_bbox() -> None:
    """NEGATIVE CONTROL. The `--bbox` absence that is CORRECT, and must not be "fixed" by a later pass.

    `climate`, `soil` and `vegetation` read a PINNED support lattice out of `agri.spatial_cell`; a
    bbox there would cut cells out of a fixed support, and each writer's own cell-count guard would
    then refuse every day. `drought` publishes one national USDM release as five class polygons, with
    no per-record spatial filter to apply. Adding `--bbox` to any of the four is not parity, it is a
    silent change of support -- which is the one change that makes a day incomparable with the
    history it extends.
    """
    for package in ("climate", "soil", "vegetation", "drought"):
        contract = _contract(package)
        assert contract.unconfigured_bbox == NOT_BBOX_BOUNDED, package
        assert "--bbox" in contract.flags_absent_on_purpose, f"{package} must say WHY it takes no bbox"
        assert "--bbox" not in _flags(package), package


def test_watersheds_is_still_the_only_writer_without_a_retry_series() -> None:
    """NEGATIVE CONTROL. Ten writers retry; this one fetches once, and that is argued rather than owed.

    One turn is one ~9,400-basin, ~47-request NHDPlus_HR walk with no cheaper attribute-only probe to
    re-poll, against a national reference layer measured to hold exactly ONE load day in its entire
    history -- so a failed turn costs nothing the next cron tick does not recover. Adding the retry
    trio "for parity" would double the most expensive fetch in this package to shorten a recovery
    nothing is waiting on.
    """
    without_retries = {package for package in PACKAGES if "--retry-attempts" not in _flags(package)}
    assert without_retries == {"watersheds"}, (
        f"the set of writers with no retry series changed to {sorted(without_retries)}; if watersheds "
        "gained one, delete its exemption, and if another writer lost one, that is a regression"
    )
    excused = _contract("watersheds").flags_absent_on_purpose
    for flag in ("--retry-attempts", "--retry-base-seconds", "--retry-max-seconds"):
        assert flag in excused, f"watersheds omits {flag} with no declared reason"


def test_the_two_record_ceiling_spellings_are_both_still_in_use() -> None:
    """NEGATIVE CONTROL. `--max-records` and `--max-records-per-day` are two knobs, not one misspelt twice.

    `sensors` caps readings across the WHOLE ROSTER for one poll, so its unit is a turn.
    `fire_detections` caps records inside ONE EXACT UTC DAY, so its unit is a day. Giving them one
    name would make a turn-scoped ceiling read as day-scoped, which is how an operator sets a cap
    several times smaller than they meant. Each writer excuses the other's spelling by name.
    """
    assert "--max-records" in _flags("sensors")
    assert "--max-records-per-day" in _flags("fire_detections")
    assert "--max-records-per-day" in _contract("sensors").flags_absent_on_purpose
    assert "--max-records" in _contract("fire_detections").flags_absent_on_purpose
