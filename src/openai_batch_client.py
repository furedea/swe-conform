"""OpenAI Files and Batch API adapter for asynchronous JSONL requests."""

import time
from collections.abc import Callable, Mapping
from typing import cast

import httpx

_DEFAULT_TIMEOUT_SECONDS = 300.0
_DEFAULT_MAX_ATTEMPTS = 3
_ERROR_BODY_LIMIT = 2000
_RETRYABLE_STATUS_CODES = frozenset({408, 429})


class OpenAIBatchError(RuntimeError):
    """An OpenAI Files or Batch request could not be completed."""


class OpenAIBatchClient:
    """Upload Batch inputs, manage jobs, and download result files."""

    __slots__ = ("_base_url", "_client", "_headers", "_max_attempts", "_retry_wait")

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
        http_client: httpx.Client | None = None,
        retry_wait: Callable[[int], None] | None = None,
    ) -> None:
        if max_attempts < 1:
            msg = "max_attempts must be at least 1"
            raise ValueError(msg)
        self._base_url = base_url.rstrip("/")
        self._max_attempts = max_attempts
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._client = http_client or httpx.Client(timeout=_DEFAULT_TIMEOUT_SECONDS)
        self._retry_wait = retry_wait or _wait_before_retry

    def upload_input(self, *, filename: str, content: bytes) -> Mapping[str, object]:
        """Upload one JSONL file with the Batch purpose."""
        response = self._request(
            "POST",
            "/files",
            files={
                "purpose": (None, b"batch"),
                "file": (filename, content, "application/jsonl"),
            },
        )
        return cast(Mapping[str, object], response.json())

    def create_batch(self, *, input_file_id: str) -> Mapping[str, object]:
        """Create one 24-hour Batch targeting the Responses API."""
        response = self._request(
            "POST",
            "/batches",
            json_body={
                "input_file_id": input_file_id,
                "endpoint": "/v1/responses",
                "completion_window": "24h",
            },
        )
        return cast(Mapping[str, object], response.json())

    def retrieve_batch(self, batch_id: str) -> Mapping[str, object]:
        """Return the current state of one Batch job."""
        response = self._request("GET", f"/batches/{batch_id}")
        return cast(Mapping[str, object], response.json())

    def download_file(self, file_id: str) -> bytes:
        """Download one OpenAI file without altering its bytes."""
        return self._request("GET", f"/files/{file_id}/content").content

    def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        self._client.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Mapping[str, object] | None = None,
        files: Mapping[str, tuple[str | None, bytes] | tuple[str, bytes, str]] | None = None,
    ) -> httpx.Response:
        for attempt in range(self._max_attempts):
            try:
                response = self._send(method, path, json_body=json_body, files=files)
                response.raise_for_status()
            except httpx.TransportError as error:
                if self._should_retry(attempt):
                    self._retry_wait(attempt)
                    continue
                msg = f"OpenAI Batch request failed after {self._max_attempts} attempts: {error}"
                raise OpenAIBatchError(msg) from error
            except httpx.HTTPStatusError as error:
                if self._is_retryable_status(error.response.status_code) and self._should_retry(attempt):
                    self._retry_wait(attempt)
                    continue
                body = error.response.text[:_ERROR_BODY_LIMIT]
                msg = f"OpenAI Batch request failed: status={error.response.status_code} body={body}"
                raise OpenAIBatchError(msg) from error
            else:
                return response
        msg = "OpenAI Batch request retry loop ended unexpectedly"
        raise OpenAIBatchError(msg)

    def _send(
        self,
        method: str,
        path: str,
        *,
        json_body: Mapping[str, object] | None,
        files: Mapping[str, tuple[str | None, bytes] | tuple[str, bytes, str]] | None,
    ) -> httpx.Response:
        url = f"{self._base_url}{path}"
        if files is not None:
            return self._client.request(method, url, headers=self._headers, files=files)
        return self._client.request(method, url, headers=self._headers, json=json_body)

    def _should_retry(self, attempt: int) -> bool:
        return attempt + 1 < self._max_attempts

    @staticmethod
    def _is_retryable_status(status_code: int) -> bool:
        return status_code in _RETRYABLE_STATUS_CODES or status_code >= 500


def _wait_before_retry(attempt: int) -> None:
    time.sleep(float(2**attempt))
