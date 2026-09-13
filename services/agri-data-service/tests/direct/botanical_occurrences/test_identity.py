"""Native-identity comparison across two releases, and the verdicts it is allowed to reach."""

from __future__ import annotations

from agri_data_service.pipeline.direct.botanical_occurrences.identity import compare_releases


def _rows(*pairs: tuple[str, str]) -> list[dict[str, str]]:
    return [{"source_record_key": key, "row_sha256": content} for key, content in pairs]


def test_two_identical_complete_releases_are_stable() -> None:
    report = compare_releases(
        _rows(("a", "1"), ("b", "2")),
        _rows(("a", "1"), ("b", "2")),
        older_release_key="older",
        newer_release_key="newer",
    )
    assert report.verdict == "stable"
    assert report.continued == ("a", "b")
    assert report.added == () and report.missing == ()


def test_an_added_record_alone_is_still_stable() -> None:
    """A collection that grew has not lost identity; growth is the ordinary case."""
    report = compare_releases(
        _rows(("a", "1")), _rows(("a", "1"), ("b", "2")), older_release_key="older", newer_release_key="newer"
    )
    assert report.verdict == "stable"
    assert report.added == ("b",)


def test_a_missing_key_is_reported_as_unstable_and_not_as_a_withdrawal() -> None:
    report = compare_releases(
        _rows(("a", "1"), ("b", "2")), _rows(("a", "1")), older_release_key="older", newer_release_key="newer"
    )
    assert report.verdict == "unstable"
    assert report.missing == ("b",)


def test_a_reused_key_with_changed_content_is_visible() -> None:
    report = compare_releases(
        _rows(("a", "1")), _rows(("a", "9")), older_release_key="older", newer_release_key="newer"
    )
    assert report.reused_with_changed_content == ("a",)
    assert report.verdict == "unstable"


def test_a_key_repeated_inside_one_release_is_reported() -> None:
    report = compare_releases(
        _rows(("a", "1"), ("a", "2")), _rows(("a", "1")), older_release_key="older", newer_release_key="newer"
    )
    assert report.duplicate_keys_in_older == ("a",)
    assert report.verdict == "unstable"


def test_a_partial_release_makes_the_comparison_inconclusive() -> None:
    """You cannot tell a withdrawn record from an untransferred one, so no verdict is offered."""
    report = compare_releases(
        _rows(("a", "1"), ("b", "2")),
        _rows(("a", "1")),
        older_release_key="older",
        newer_release_key="newer",
        newer_outcome="partial",
    )
    assert report.verdict == "inconclusive"
    assert report.missing == ("b",), "the fact is still reported; only the verdict is withheld"


def test_the_report_renders_counts_rather_than_every_key() -> None:
    report = compare_releases(_rows(("a", "1")), _rows(("a", "1")), older_release_key="o", newer_release_key="n")
    assert '"continued": 1' in report.as_json()
