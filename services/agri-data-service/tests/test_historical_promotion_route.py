"""Receiver state-machine tests that do not require a live target database."""

from uuid import uuid4

import pytest

from agri_data_service.models.historical_promotion import HistoricalPromotionBundle, HistoricalPromotionState
from agri_data_service.routes.historical_promotion import _locked_bundle, _PromotionAbortError


class _Result:
    def __init__(self, bundle: HistoricalPromotionBundle) -> None:
        self.bundle = bundle

    def scalar_one_or_none(self) -> HistoricalPromotionBundle:
        return self.bundle


class _Session:
    def __init__(self, bundle: HistoricalPromotionBundle) -> None:
        self.bundle = bundle

    async def execute(self, _statement: object) -> _Result:
        return _Result(self.bundle)


@pytest.mark.asyncio
async def test_finalized_bundle_can_be_locked_only_for_idempotent_finalization() -> None:
    bundle = HistoricalPromotionBundle(
        manifest_checksum="0" * 64,
        manifest_json={},
        release_set_id=uuid4(),
        state=HistoricalPromotionState.FINALIZED,
        expected_chunk_count=1,
        expected_record_count=1,
        received_by="historical-promoter",
    )
    session = _Session(bundle)

    assert await _locked_bundle(session, uuid4(), allow_finalized=True) is bundle  # type: ignore[arg-type]
    with pytest.raises(_PromotionAbortError, match="historical_bundle_not_receiving"):
        await _locked_bundle(session, uuid4())  # type: ignore[arg-type]
