"""`app.py::create_app` now configures Wave O's shared logging chain instead of its own copy.

See `foundation/observability/AGENTS.md` "Logging contract" -- COV2-07 is exactly this: the web
process is redacted from GL-1, not left on its own inline `structlog.configure(...)` block.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from agri_data_service import app as app_module

if TYPE_CHECKING:
    import pytest

    from agri_data_service.app import AgriApp


def test_web_process_lines_are_redacted_json(
    app: AgriApp, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    del app  # building it is what calls create_app(), which is what this test is really about
    monkeypatch.setenv("SOME_SERVICE_API_KEY", "supersecretvalue123")
    app_module.logger.info("web_process_canary_event", token="supersecretvalue123")
    captured = capsys.readouterr()
    lines = [json.loads(line) for line in captured.out.splitlines() if line.strip()]
    matching = [line for line in lines if line.get("event") == "web_process_canary_event"]
    assert matching, "expected the canary event to render as one JSON line on stdout"
    payload = matching[0]
    assert payload["level"] == "info"
    assert {"event", "level", "timestamp", "service"} <= payload.keys()
    assert "supersecretvalue123" not in json.dumps(payload)
