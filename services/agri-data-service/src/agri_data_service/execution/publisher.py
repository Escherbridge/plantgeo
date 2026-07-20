"""Bounded, retryable publication from local storage to the operational API."""

import base64
import hashlib
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from agri_data_service.execution.contracts import (
    CommitRunRequest,
    LocalOutput,
    LocalRunState,
    PublicationManifest,
    PublicationPhase,
    StageRunRequest,
    UploadOutputRequest,
    ValidationReport,
    validation_report_matches_output,
)
from agri_data_service.execution.local_store import LocalRunStore

RETRYABLE_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}
MIN_TOKEN_LENGTH = 32
MIN_TOKEN_DIVERSITY = 10


class PublicationError(RuntimeError):
    """A bounded publication attempt could not complete."""


class BoundedPublisher:
    """Publish one frozen local run with a durable per-output cursor."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        base_url: str,
        token: str,
        max_artifact_bytes: int,
        max_validation_bytes: int,
        max_outputs: int,
        max_run_artifact_bytes: int,
        max_run_validation_bytes: int,
        retry_attempts: int,
        retry_base_seconds: float,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.base_url = _validated_base_url(base_url)
        if (
            len(token) < MIN_TOKEN_LENGTH
            or token != token.strip()
            or any(character.isspace() for character in token)
            or len(set(token)) < MIN_TOKEN_DIVERSITY
        ):
            raise ValueError("publication token must contain 32 diverse non-whitespace characters")
        if (
            max_artifact_bytes <= 0
            or max_validation_bytes <= 0
            or max_outputs <= 0
            or max_run_artifact_bytes <= 0
            or max_run_validation_bytes <= 0
        ):
            raise ValueError("publication byte limits must be positive")
        if retry_attempts <= 0:
            raise ValueError("retry_attempts must be positive")
        if retry_base_seconds < 0:
            raise ValueError("retry_base_seconds cannot be negative")
        self.max_artifact_bytes = max_artifact_bytes
        self.max_validation_bytes = max_validation_bytes
        self.max_outputs = max_outputs
        self.max_run_artifact_bytes = max_run_artifact_bytes
        self.max_run_validation_bytes = max_run_validation_bytes
        self.retry_attempts = retry_attempts
        self.retry_base_seconds = retry_base_seconds
        self.sleep = sleep
        self._owns_client = client is None
        self.client = client or httpx.Client(
            headers={"Authorization": f"Bearer {token}"},
            timeout=httpx.Timeout(30.0, connect=10.0),
        )
        if client is not None:
            self.client.headers["Authorization"] = f"Bearer {token}"

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def publish(
        self,
        store: LocalRunStore,
        run_id: uuid.UUID,
        *,
        product: str,
        scope_key: str,
    ) -> dict[str, Any]:
        manifest = store.load(run_id)
        if manifest.state not in {LocalRunState.VALIDATED, LocalRunState.PUBLISHING}:
            raise PublicationError("local run must contain validated outputs before publication")
        publication_manifest = manifest.publication_manifest()
        self._validate_manifest_bounds(publication_manifest)
        manifest_checksum = publication_manifest.checksum()
        try:
            store.begin_publication(
                run_id,
                product=product,
                scope_key=scope_key,
            )
        except ValueError as exc:
            raise PublicationError(str(exc)) from exc
        try:
            self._request(
                store,
                run_id,
                "POST",
                "/runs/stage",
                json=StageRunRequest(
                    manifest=publication_manifest,
                    product=product,
                    scope_key=scope_key,
                ).model_dump(mode="json"),
            )
            manifest = store.update_publication(run_id, phase=PublicationPhase.UPLOADING)
            outputs = publication_manifest.outputs
            if manifest.publication.next_output_index > len(outputs):
                raise PublicationError("local publication cursor exceeds the output manifest")
            for index in range(manifest.publication.next_output_index, len(outputs)):
                output = outputs[index]
                upload_request = self._load_output_request(store, run_id, output, publication_manifest)
                self._request(
                    store,
                    run_id,
                    "PUT",
                    f"/runs/{run_id}/outputs/{quote(output.output_key, safe='')}",
                    json=upload_request.model_dump(mode="json"),
                )
                store.update_publication(
                    run_id,
                    phase=PublicationPhase.UPLOADING,
                    next_output_index=index + 1,
                )
            store.update_publication(run_id, phase=PublicationPhase.COMMITTING)
            response = self._request(
                store,
                run_id,
                "POST",
                f"/runs/{run_id}/commit",
                json=CommitRunRequest(
                    manifest_checksum=manifest_checksum,
                    release_set_manifest_checksum=(publication_manifest.release_set_manifest_checksum),
                ).model_dump(mode="json"),
            )
            response_payload = response.json()
            if not isinstance(response_payload, dict):
                raise PublicationError("publication API returned a non-object response")
            result: dict[str, Any] = response_payload
            revision = result.get("revision")
            if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
                raise PublicationError("publication API returned an invalid revision")
            store.update_publication(
                run_id,
                phase=PublicationPhase.PUBLISHED,
                next_output_index=len(outputs),
                remote_revision=revision,
            )
            return result
        except (KeyError, OSError, TypeError, ValueError, httpx.HTTPError, PublicationError) as exc:
            error = str(exc)[:2000]
            store.update_publication(
                run_id,
                phase=PublicationPhase.FAILED,
                last_error=error,
            )
            if isinstance(exc, PublicationError):
                raise
            raise PublicationError(error) from exc

    def _load_output_request(
        self,
        store: LocalRunStore,
        run_id: uuid.UUID,
        output: LocalOutput,
        manifest: PublicationManifest,
    ) -> UploadOutputRequest:
        artifact_path = store.resolve_run_path(run_id, output.relative_path)
        report_path = store.resolve_run_path(run_id, output.validation_report_relative_path)
        artifact_bytes = _read_bounded_stable(
            artifact_path,
            self.max_artifact_bytes,
            label=f"artifact {output.output_key}",
            shard_hint=True,
        )
        artifact_checksum = _sha256(artifact_bytes)
        artifact_size = len(artifact_bytes)
        if artifact_checksum != output.checksum_sha256 or artifact_size != output.size_bytes:
            raise PublicationError(f"artifact changed after validation: {output.output_key}")
        report_bytes = _read_bounded_stable(
            report_path,
            self.max_validation_bytes,
            label=f"validation report {output.output_key}",
        )
        report_checksum = _sha256(report_bytes)
        if (
            report_checksum != output.validation_report_sha256
            or len(report_bytes) != output.validation_report_size_bytes
        ):
            raise PublicationError(f"validation report changed: {output.output_key}")
        report = ValidationReport.model_validate_json(report_bytes)
        if not validation_report_matches_output(
            report,
            output,
            run_plan_checksum=manifest.run_plan_checksum,
            release_set_manifest_checksum=manifest.release_set_manifest_checksum,
        ):
            raise PublicationError(f"validation report no longer binds output: {output.output_key}")
        return UploadOutputRequest(
            descriptor=output,
            content_base64=base64.b64encode(artifact_bytes).decode("ascii"),
            validation_report_base64=base64.b64encode(report_bytes).decode("ascii"),
        )

    def _validate_manifest_bounds(self, manifest: PublicationManifest) -> None:
        if len(manifest.outputs) > self.max_outputs:
            raise PublicationError("local run declares too many publication outputs")
        if manifest.declared_artifact_bytes > self.max_run_artifact_bytes:
            raise PublicationError("local run exceeds the aggregate artifact byte quota")
        if manifest.declared_validation_bytes > self.max_run_validation_bytes:
            raise PublicationError("local run exceeds the aggregate validation byte quota")
        if any(output.size_bytes > self.max_artifact_bytes for output in manifest.outputs):
            raise PublicationError(
                "local run contains an artifact above the per-file quota; split it into deterministic bounded shards"
            )
        if any(output.validation_report_size_bytes > self.max_validation_bytes for output in manifest.outputs):
            raise PublicationError("local run contains a validation report above the per-file quota")

    def _request(
        self,
        store: LocalRunStore,
        run_id: uuid.UUID,
        method: str,
        path: str,
        *,
        json: dict[str, Any],
    ) -> httpx.Response:
        last_error = "publication request failed"
        for attempt in range(1, self.retry_attempts + 1):
            store.update_publication(
                run_id,
                phase=store.load(run_id).publication.phase,
                attempt_increment=1,
            )
            try:
                response = self.client.request(method, f"{self.base_url}{path}", json=json)
                if response.is_success:
                    return response
                last_error = f"publication API returned HTTP {response.status_code}"
                if response.status_code not in RETRYABLE_STATUS_CODES:
                    raise PublicationError(last_error)
            except httpx.TransportError as exc:
                last_error = f"publication transport failed: {type(exc).__name__}"
            if attempt < self.retry_attempts:
                self.sleep(min(self.retry_base_seconds * (2 ** (attempt - 1)), 30.0))
        raise PublicationError(last_error)


def _validated_base_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("publication API URL must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("publication API URL cannot contain credentials, query, or fragment")
    localhost = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and not localhost:
        raise ValueError("publication API requires HTTPS except on loopback")
    return value.rstrip("/")


def _read_bounded_stable(
    path: Path,
    max_bytes: int,
    *,
    label: str,
    shard_hint: bool = False,
) -> bytes:
    """Read one stable file without allocating beyond the configured bound."""
    before = path.stat()
    with path.open("rb") as file:
        content = file.read(max_bytes + 1)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise PublicationError(f"{label} changed while preparing publication")
    if len(content) > max_bytes or after.st_size > max_bytes:
        message = f"{label} exceeds {max_bytes} bytes"
        if shard_hint:
            message += "; split it into deterministic bounded shards"
        raise PublicationError(message)
    if len(content) != after.st_size:
        raise PublicationError(f"{label} could not be read completely")
    return content


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
