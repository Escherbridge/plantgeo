"""`foundation/observability/redaction.py`: never a secret, a statement, or a keyed URL reaches a line.

See its AGENTS.md entry "Redaction" for the pipeline order this exercises: exact-value scrub, then
`truncate_leaf`, then the regex scrubs (`redact_leaf`); `redact_for_log` is the same minus the
truncation step.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy.exc import OperationalError

from agri_data_service.foundation.observability import redaction

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

# --- revision 1's eleven (ledger pattern, exact-value scrub, SQL/DSN/query/bearer, structured walk) --


def test_redact_strict_substitutes_url_shaped_tokens_whole() -> None:
    assert redaction.redact_strict("fetch https://user:pw@host/path?key=abc failed") == "fetch [redacted] failed"


def test_redact_strict_substitutes_user_at_host_tokens() -> None:
    assert redaction.redact_strict("contact admin@example.com now") == "contact [redacted] now"


def test_redact_strict_substitutes_bare_query_tails() -> None:
    assert redaction.redact_strict("token leaked ?apikey=CANARYVALUE here") == "token leaked [redacted] here"


def test_exact_value_scrub_replaces_a_live_env_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPEN_METEO_API_KEY", "supersecretvalue123")
    scrubbed = redaction.exact_value_scrub("request used supersecretvalue123 as the key")
    assert "supersecretvalue123" not in scrubbed
    assert "[redacted:OPEN_METEO_API_KEY]" in scrubbed


def test_exact_value_scrub_ignores_plantgeo_prefixed_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLANTGEO_TURN_TOKEN", "notactuallyasecretvalue")
    scrubbed = redaction.exact_value_scrub("carrying notactuallyasecretvalue along")
    assert "notactuallyasecretvalue" in scrubbed


def test_exact_value_scrub_ignores_short_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOME_TOKEN", "short")
    scrubbed = redaction.exact_value_scrub("value is short here")
    assert scrubbed == "value is short here"


def test_sql_block_is_cut_from_the_marker_onward() -> None:
    message = "(sqlite3.OperationalError) boom\n[SQL: SELECT * FROM secrets]\n[parameters: (1,)]"
    scrubbed = redaction.redact_for_log(message)
    assert "SELECT * FROM secrets" not in scrubbed
    assert scrubbed.endswith(redaction.SQL_REDACTED_PLACEHOLDER)


def test_dsn_userinfo_is_replaced() -> None:
    scrubbed = redaction.redact_for_log("dsn postgresql://user:hunter2@db.internal/plantgeo")
    assert "hunter2" not in scrubbed
    assert "postgresql://[redacted]@db.internal/plantgeo" in scrubbed


def test_dsn_userinfo_with_an_unencoded_at_sign_is_fully_replaced() -> None:
    """A greedy (not first-`@`) userinfo match: the password itself contains an unencoded `@`."""
    scrubbed = redaction.redact_for_log("dsn postgresql://user:p@ssw0rdSECRET@db.internal/plantgeo")
    assert "p@ssw0rdSECRET" not in scrubbed
    assert "postgresql://[redacted]@db.internal/plantgeo" in scrubbed


def test_secret_query_parameters_are_redacted() -> None:
    scrubbed = redaction.redact_for_log("GET /area/csv?map_key=CANARY&other=1")
    assert "CANARY" not in scrubbed
    assert "map_key=[redacted]" in scrubbed


def test_underscore_prefixed_query_parameters_are_redacted() -> None:
    """`\\b` does not fire after `_`: `client_secret=`/`refresh_token=` previously survived whole."""
    scrubbed = redaction.redact_for_log("GET /cb?client_secret=CANARY1&refresh_token=CANARY2")
    assert "CANARY1" not in scrubbed
    assert "CANARY2" not in scrubbed
    assert "client_secret=[redacted]" in scrubbed
    assert "refresh_token=[redacted]" in scrubbed


def test_quoted_key_value_secrets_are_redacted() -> None:
    """The `'name': 'value'` shape a dict repr or an echoed JSON body takes, distinct from `name=value`."""
    scrubbed = redaction.redact_for_log("{'x-api-key': 'HDRSECRET123', \"password\": \"hunter2hunter2\"}")
    assert "HDRSECRET123" not in scrubbed
    assert "hunter2hunter2" not in scrubbed


def test_bearer_and_authorization_are_redacted() -> None:
    scrubbed = redaction.redact_for_log("Authorization: Bearer abc.def.ghi")
    assert "abc.def.ghi" not in scrubbed


def test_authorization_scheme_and_credential_are_both_redacted() -> None:
    """A Basic/Token credential previously survived: the old pattern consumed only the scheme word."""
    scrubbed = redaction.redact_for_log("Authorization: Basic dXNlcjpzZWNyZXQ=")
    assert "dXNlcjpzZWNyZXQ=" not in scrubbed


def test_redact_value_walks_nested_mappings_and_lists() -> None:
    payload = {"headers": [{"Authorization": "Bearer abc123456789"}], "note": "safe"}
    redacted = redaction.redact_value(payload)
    assert redacted["headers"][0]["Authorization"] == redaction.REDACTED_PLACEHOLDER
    assert redacted["note"] == "safe"


# --- named additions ------------------------------------------------------------------------------


def test_dotenv_and_dsn_password_values_are_scrubbed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text('CDSAPI_KEY="dotenvsecretvalue99"\n', encoding="utf-8")
    scrubbed = redaction.exact_value_scrub("carrying dotenvsecretvalue99 forward")
    assert "dotenvsecretvalue99" not in scrubbed

    monkeypatch.setenv("LOCAL_SOURCE_LOADER_DATABASE_URL", "postgresql://svc:dsnpassword123@db/plantgeo")
    scrubbed_password = redaction.exact_value_scrub("saw dsnpassword123 in a log line")
    assert "dsnpassword123" not in scrubbed_password


def test_dsn_shaped_url_env_names_are_scrubbed_by_password(monkeypatch: pytest.MonkeyPatch) -> None:
    """A `*_DATABASE_URL`/`*_URL` name outside the fixed exact-name set is still caught when its VALUE
    is DSN-shaped and carries a password (security review: `RECEIVER_WRITER_DATABASE_URL` and
    similar Settings fields matched no suffix and no exact name before this).
    """
    monkeypatch.setenv("SOME_OTHER_SERVICE_DATABASE_URL", "postgresql://svc:railwaypassword123@host/db")
    scrubbed = redaction.exact_value_scrub("dsn was postgresql://svc:railwaypassword123@host/db")
    assert "railwaypassword123" not in scrubbed


def test_truncation_never_leaves_a_secret_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "straddlingsecretvalueabc")
    # The secret sits at the very FRONT, far from the truncation cut point at the end, and next to no
    # separator: truncation alone (which only ever drops TRAILING content, cut at a separator inside
    # its own tail window) could not have removed it by coincidence -- the previous version of this
    # test put the secret right after a space that happened to fall inside the tail-search window, so
    # `truncate_leaf` alone already dropped it even with the exact-value scrub disabled (security
    # review).
    filler = "z" * (redaction.LEAF_TRUNCATE_BYTES * 2)
    value = "straddlingsecretvalueabc" + filler
    redacted = redaction.redact_leaf(value)
    assert "straddlingsecretvalueabc" not in redacted
    assert redacted.startswith("[redacted:R2_SECRET_ACCESS_KEY]")


def test_key_match_is_casefolded_and_manifest_key_survives() -> None:
    payload = {"Authorization": "Bearer abc123456789", "manifest": {"key": "burn-severity-manifest-key"}}
    redacted = redaction.redact_value(payload)
    assert redacted["Authorization"] == redaction.REDACTED_PLACEHOLDER
    # The bare "key" is deliberately excluded from the structured key set.
    assert redacted["manifest"]["key"] == "burn-severity-manifest-key"


def test_realistic_key_names_beyond_the_exact_set_are_redacted() -> None:
    """Key-based redaction is a predicate, not only the historical exact set (security review)."""
    payload = {
        "open_meteo_api_key": "OMSECRET123",
        "cdsapi_key": "CDSSECRET123",
        "cookie": "session=abc",
        "receiver_writer_database_url": "postgresql://u:p@h/db",
    }
    redacted = redaction.redact_value(payload)
    for key in payload:
        assert redacted[key] == redaction.REDACTED_PLACEHOLDER, key


def test_url_shaped_dict_key_is_itself_redacted() -> None:
    """A dict KEYED BY a URL that embeds a secret publishes it through the key, not only a value."""
    payload = {"https://h/v1?apikey=CANARYKEY123": 3}
    redacted = redaction.redact_value(payload)
    assert "CANARYKEY123" not in "".join(str(key) for key in redacted)


def test_depth_limit_stubs() -> None:
    nested: dict[str, object] = {"value": "leaf"}
    node = nested
    for _ in range(redaction.MAX_REDACTION_DEPTH + 4):
        node["child"] = {"value": "leaf"}
        node = node["child"]  # type: ignore[assignment]
    redacted = redaction.redact_value(nested)
    node = redacted
    depth = 0
    while isinstance(node, dict) and "child" in node:
        node = node["child"]
        depth += 1
    assert node == redaction.DEPTH_LIMIT_PLACEHOLDER


# --- describe_error --------------------------------------------------------------------------------


def test_describe_error_gives_only_the_class_name_for_sqlalchemy_errors() -> None:
    error = OperationalError("SELECT * FROM secrets", {}, Exception("boom"))
    assert redaction.describe_error(error) == "OperationalError"


def test_describe_error_redacts_and_clamps_other_exceptions() -> None:
    error = ValueError("token leaked ?apikey=CANARYVALUE here")
    described = redaction.describe_error(error)
    assert "CANARYVALUE" not in described
    assert len(described) <= redaction.DESCRIBE_ERROR_MAX_LENGTH


def test_register_secret_values_accepts_bytes_and_ignores_bad_input() -> None:
    """`register_secret_values` must fail SAFE, never raise, for any input shape (security review)."""
    redaction.register_secret_values(b"registeredbytessecret123")
    scrubbed = redaction.exact_value_scrub("carrying registeredbytessecret123 forward")
    assert "registeredbytessecret123" not in scrubbed
    # None and an unrelated object are silently ignored rather than raised.
    redaction.register_secret_values(None)
    redaction.register_secret_values(12345)
