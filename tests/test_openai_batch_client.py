"""Tests for the OpenAI Batch and Files API adapter."""

import json

import httpx

import openai_batch_client


def test_client_uploads_creates_retrieves_and_downloads_batch_files() -> None:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v1/files" and request.method == "POST":
            body = request.content.decode()
            assert 'name="purpose"' in body
            assert "batch" in body
            assert 'filename="batch_input.jsonl"' in body
            return httpx.Response(200, json={"id": "file-input"})
        if request.url.path == "/v1/batches" and request.method == "POST":
            assert json.loads(request.content) == {
                "completion_window": "24h",
                "endpoint": "/v1/responses",
                "input_file_id": "file-input",
            }
            return httpx.Response(200, json={"id": "batch-1", "status": "validating"})
        if request.url.path == "/v1/batches/batch-1":
            return httpx.Response(200, json={"id": "batch-1", "status": "completed"})
        if request.url.path == "/v1/files/file-output/content":
            return httpx.Response(200, content=b'{"custom_id":"candidate-0001"}\n')
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    http_client = httpx.Client(transport=httpx.MockTransport(handle))
    client = openai_batch_client.OpenAIBatchClient(api_key="sk-test", http_client=http_client)

    uploaded = client.upload_input(filename="batch_input.jsonl", content=b"{}\n")
    created = client.create_batch(input_file_id=str(uploaded["id"]))
    retrieved = client.retrieve_batch(str(created["id"]))
    content = client.download_file("file-output")

    assert retrieved["status"] == "completed"
    assert content == b'{"custom_id":"candidate-0001"}\n'
    assert all(request.headers["authorization"] == "Bearer sk-test" for request in requests)


def test_client_retries_retryable_server_errors() -> None:
    attempts = 0

    def handle(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503, text="temporarily unavailable")
        return httpx.Response(200, json={"id": "batch-1", "status": "completed"})

    http_client = httpx.Client(transport=httpx.MockTransport(handle))
    client = openai_batch_client.OpenAIBatchClient(
        api_key="sk-test",
        http_client=http_client,
        retry_wait=lambda _attempt: None,
    )

    result = client.retrieve_batch("batch-1")

    assert result["status"] == "completed"
    assert attempts == 2
