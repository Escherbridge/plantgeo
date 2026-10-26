"""The daily `--current-snapshots` turn captures current MTBS only; the release-day census is `mtbs-forward`'s."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from agri_data_service.ingest.mtbs import burn_severity_bounding_box
from agri_data_service.pipeline.direct.burn_severity import daily, forward
from agri_data_service.pipeline.direct.burn_severity.forward import BurnSeverityForwardConfig

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


class _EmptyQueueStorage:
    """An availability store holding no staging queue yet, so a capture is due and nothing is eligible."""

    def read(self, _key: str, *, max_bytes: int) -> None:
        del max_bytes


def _config() -> BurnSeverityForwardConfig:
    return BurnSeverityForwardConfig(
        max_days=1,
        time_budget_seconds=600.0,
        retry_attempts=1,
        retry_base_seconds=0.0,
        retry_max_seconds=0.0,
        contention_timeout_seconds=1.0,
        bbox=",".join(str(ordinate) for ordinate in burn_severity_bounding_box()),
        today=datetime.now(UTC).date(),
        current_snapshots=True,
    )


async def test_a_due_capture_turn_publishes_no_historical_release_days(monkeypatch: pytest.MonkeyPatch) -> None:
    """With budget left after the capture, the turn still never walks the historical census."""
    historical_calls: list[object] = []

    async def _historical_census(config: object) -> dict[str, object]:
        historical_calls.append(config)
        return {"status": "published"}

    def _capture(directory: Path) -> None:
        directory.mkdir(parents=True)
        (directory / "manifest.json").write_bytes(b'{"capture": "unchanged"}')

    monkeypatch.setattr(forward, "run_burn_severity_forward", _historical_census)
    monkeypatch.setattr(daily, "run_burn_severity_forward", _historical_census, raising=False)
    monkeypatch.setattr(daily.BotoAvailabilityStorage, "from_settings", staticmethod(_EmptyQueueStorage))
    monkeypatch.setattr(daily, "capture_snapshot", _capture)
    monkeypatch.setattr(daily, "record_unchanged_capture", lambda _storage, **_kwargs: True)

    result = await daily._run_daily_owned(_config())

    assert historical_calls == []
    assert set(result) == {"status", "publication", "source"}
    assert result["status"] == "checked"
    assert result["source"]["status"] == "checked_unchanged"  # type: ignore[index]
