"""WQ-6 (spec §4.9.2, FR-34): `receipts/source-usage/<YYYY-MM>.json`, written once per closed UTC month.

Drives `usage_receipt.write_closed_month_receipt` (the call the executor's daily upkeep makes) over the real
`usage_report.py` loaders against a ledger fake that answers their two statements per month, and an in-memory
object-store backend (`tests/parquet/test_objectstore_writer.py::RecordingBackend`).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Final

from agri_data_service.execution import usage_report
from agri_data_service.execution.usage_receipt import UsageReceiptStore, write_closed_month_receipt
from tests.parquet.test_objectstore_writer import RecordingBackend

PREFIX: Final = "plantgeo"
SEPTEMBER_KEY: Final = "plantgeo/receipts/source-usage/2026-09.json"
OCTOBER_KEY: Final = "plantgeo/receipts/source-usage/2026-10.json"


def _usage_row(started_at: datetime, *, charged: float) -> dict[str, object]:
    """One `select_provider_usage.sql` row: a metered soil turn on the paid pool."""
    return {
        "attempt_id": uuid.uuid4(),
        "lane_id": "soil-era5-land-direct-forward",
        "started_at": started_at,
        "started_on": started_at.date(),
        "turn_outcome": "completed",
        "exit_class": "ok",
        "requests": 33,
        "elapsed_seconds": 60,
        "charged": charged,
        "suspect": 0,
        "host": "customer-archive-api.open-meteo.com",
        "pool": "open-meteo-paid",
        "http_requests": 33,
        "weighted_calls_metered": charged,
    }


class _Rows:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def mappings(self) -> _Rows:
        return self

    def first(self) -> dict[str, object] | None:
        return self._rows[0] if self._rows else None

    def all(self) -> list[dict[str, object]]:
        return list(self._rows)


class _Savepoint:
    async def __aenter__(self) -> _Savepoint:
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        return False


class MonthlyLedger:
    """The attempts ledger, by month: `month_to_date` answers for the month of its `now`, the usage feed by window."""

    def __init__(self) -> None:
        self.charged_by_month: dict[tuple[int, int], float] = {}
        self.attempts: list[dict[str, object]] = []

    def record(self, started_at: datetime, *, charged: float) -> None:
        month = (started_at.year, started_at.month)
        self.charged_by_month[month] = self.charged_by_month.get(month, 0.0) + charged
        self.attempts.append(_usage_row(started_at, charged=charged))

    async def execute(self, statement: object, parameters: dict[str, object] | None = None) -> _Rows:
        params = dict(parameters or {})
        if statement is usage_report._SELECT_MONTH_TO_DATE:
            now = params["now"]
            assert isinstance(now, datetime)
            paid = params["pool"] == "open-meteo-paid"
            charged = self.charged_by_month.get((now.year, now.month), 0.0) if paid else 0.0
            return _Rows(
                [
                    {
                        "pool": params["pool"],
                        "epoch_at": datetime(2026, 9, 1, tzinfo=UTC),
                        "metered_count": 1 if charged else 0,
                        "reported_count": 0,
                        "suspect_basis_count": 0,
                        "not_spawned_count": 0,
                        "lost_count": 0,
                        "charged": charged,
                        "suspect": 0,
                    }
                ]
            )
        if statement is usage_report._SELECT_PROVIDER_USAGE:
            since, until = params["since"], params["until"]
            assert isinstance(since, datetime)
            assert isinstance(until, datetime)
            return _Rows([row for row in self.attempts if since <= row["started_at"] < until])  # type: ignore[operator]
        raise AssertionError(f"the receipt executed an unexpected statement: {statement}")

    def begin_nested(self) -> _Savepoint:
        return _Savepoint()


def _receipt(backend: RecordingBackend, key: str) -> dict[str, object]:
    return json.loads(backend.objects[key])


async def test_monthly_usage_receipt_is_written_once_per_closed_month() -> None:
    """September's receipt appears once September has closed and settled, carries September's figures (never
    October's), and is never rewritten -- not even when later ledger rows would change its numbers."""
    ledger = MonthlyLedger()
    ledger.record(datetime(2026, 9, 10, 6, 50, tzinfo=UTC), charged=1570)
    ledger.record(datetime(2026, 9, 30, 18, 50, tzinfo=UTC), charged=1570)
    ledger.record(datetime(2026, 10, 1, 0, 50, tzinfo=UTC), charged=1570)
    backend = RecordingBackend()
    store = UsageReceiptStore(backend=backend, prefix=f"{PREFIX}/")

    async def daily_pass(now: datetime) -> str:
        outcome, _ = await write_closed_month_receipt(ledger, store=store, now=now)  # type: ignore[arg-type]
        return outcome

    during_september = await daily_pass(datetime(2026, 9, 28, 6, 0, tzinfo=UTC))
    first_day_of_october = await daily_pass(datetime(2026, 10, 1, 6, 0, tzinfo=UTC))
    written = await daily_pass(datetime(2026, 10, 2, 6, 0, tzinfo=UTC))
    september_bytes = backend.objects[SEPTEMBER_KEY]
    ledger.record(datetime(2026, 9, 30, 23, 59, tzinfo=UTC), charged=9999)  # a late-settling September row
    next_day = await daily_pass(datetime(2026, 10, 3, 6, 0, tzinfo=UTC))
    november = await daily_pass(datetime(2026, 11, 2, 6, 0, tzinfo=UTC))

    assert (during_september, first_day_of_october, written, next_day, november) == (
        "written",
        "not_yet_settled",
        "written",
        "already_present",
        "written",
    )
    assert sorted(backend.objects) == [
        "plantgeo/receipts/source-usage/2026-08.json",
        SEPTEMBER_KEY,
        OCTOBER_KEY,
    ], "August's receipt was written by the pass during September; each month exactly once"
    assert backend.objects[SEPTEMBER_KEY] == september_bytes, "a written month is never rewritten"
    september = _receipt(backend, SEPTEMBER_KEY)
    assert september["month"] == "2026-09"
    assert september["window"] == {"since": "2026-09-01T00:00:00+00:00", "until": "2026-10-01T00:00:00+00:00"}
    pools = september["pools"]
    assert isinstance(pools, dict)
    assert pools["open-meteo-paid"]["charged"] == 3140  # noqa: PLR2004 - the two September turns, not October's
    assert pools["open-meteo-paid"]["budget"]["monthly_cap"] == 5_000_000  # noqa: PLR2004 - lanes/_providers/open-meteo.toml
    (soil,) = september["by_lane"]  # type: ignore[misc]
    assert soil["lane_id"] == "soil-era5-land-direct-forward"
    assert soil["attempts"] == 2  # noqa: PLR2004 - the two September attempts
    assert backend.content_types[SEPTEMBER_KEY] == "application/json"
    assert _receipt(backend, OCTOBER_KEY)["pools"]["open-meteo-paid"]["charged"] == 1570  # type: ignore[index]  # noqa: PLR2004
