"""Tests for Markdown guideline Batch API cost pilots."""

import csv
import json
from pathlib import Path
from typing import cast

from pytest_mock import MockerFixture

import github_client
import markdown_batch
import openai_responses_client


def _candidate(index: int, *, size_bytes: int) -> markdown_batch.SizedMarkdownCandidate:
    return markdown_batch.SizedMarkdownCandidate(
        candidate=markdown_batch.MarkdownCandidate(
            repository=f"example/project-{index % 3}",
            revision=f"{index + 1:040x}",
            path=f"docs/README-{index}.md",
            url=f"https://github.com/example/project-{index % 3}/blob/{index + 1:040x}/docs/README-{index}.md",
            matched_terms=("readme",),
        ),
        size_bytes=size_bytes,
    )


def test_stratified_sample_selects_four_candidates_from_each_size_quintile() -> None:
    candidates = tuple(_candidate(index, size_bytes=index + 1) for index in range(100))

    sampled = markdown_batch.stratified_sample(
        candidates,
        sample_size=20,
        sample_seed=20260806,
    )

    assert len(sampled) == 20
    assert [sum(item.stratum == stratum for item in sampled) for stratum in range(1, 6)] == [4, 4, 4, 4, 4]
    assert {item.stratum_population for item in sampled} == {20}
    assert [item.custom_id for item in sampled] == [f"candidate-{index:04d}" for index in range(1, 21)]


def test_stratified_sample_is_reproducible_for_the_same_seed() -> None:
    candidates = tuple(_candidate(index, size_bytes=index + 1) for index in range(100))

    first = markdown_batch.stratified_sample(candidates, sample_size=20, sample_seed=20260806)
    second = markdown_batch.stratified_sample(candidates, sample_size=20, sample_seed=20260806)

    assert first == second


def test_all_candidates_selects_every_file_in_identity_order() -> None:
    candidates = tuple(reversed(tuple(_candidate(index, size_bytes=index + 1) for index in range(7))))

    selected = markdown_batch.all_candidates(candidates)

    assert len(selected) == 7
    assert [item.custom_id for item in selected] == [f"candidate-{index:04d}" for index in range(1, 8)]
    assert [item.sized_candidate.candidate.identity for item in selected] == sorted(
        item.candidate.identity for item in candidates
    )
    assert {item.stratum for item in selected} == {0}


def test_batch_request_contains_exactly_one_markdown_file() -> None:
    sampled = markdown_batch.SampledMarkdownCandidate(
        custom_id="candidate-0001",
        sized_candidate=_candidate(1, size_bytes=12),
        stratum=1,
        stratum_population=20,
    )

    request = markdown_batch.batch_request(
        sampled,
        content="# Rules\n\nUse snake_case.\n",
        model="gpt-5.6-luna",
        reasoning_effort="medium",
    )

    assert request["custom_id"] == "candidate-0001"
    assert request["method"] == "POST"
    assert request["url"] == "/v1/responses"
    body = cast(dict[str, object], request["body"])
    assert body["model"] == "gpt-5.6-luna"
    assert body["reasoning"] == {"effort": "medium"}
    assert body["store"] is False
    input_document = json.loads(str(body["input"]))
    assert input_document["content"] == "# Rules\n\nUse snake_case.\n"


def test_batch_request_uses_the_configured_max_output_tokens() -> None:
    sampled = markdown_batch.SampledMarkdownCandidate(
        custom_id="candidate-0001",
        sized_candidate=_candidate(1, size_bytes=12),
        stratum=1,
        stratum_population=20,
    )

    request = markdown_batch.batch_request(
        sampled,
        content="# Rules\n\nUse snake_case.\n",
        model="gpt-5.6-luna",
        reasoning_effort="max",
        max_output_tokens=2_000,
    )

    body = cast(dict[str, object], request["body"])
    assert body["max_output_tokens"] == 2_000


def test_batch_request_reads_classification_instructions_from_the_prompt_file(
    mocker: MockerFixture,
) -> None:
    instructions = mocker.patch(
        "markdown_batch._classification_instructions",
        autospec=True,
        return_value="External classification instructions.\n",
    )
    sampled = markdown_batch.SampledMarkdownCandidate(
        custom_id="candidate-0001",
        sized_candidate=_candidate(1, size_bytes=12),
        stratum=1,
        stratum_population=20,
    )

    request = markdown_batch.batch_request(
        sampled,
        content="# Rules\n\nUse snake_case.\n",
        model="gpt-5.6-luna",
        reasoning_effort="medium",
    )

    body = cast(dict[str, object], request["body"])
    assert body["instructions"] == "External classification instructions.\n"
    instructions.assert_called_once_with()


def test_batch_request_uses_classification_prompt_and_schema_files() -> None:
    sampled = markdown_batch.SampledMarkdownCandidate(
        custom_id="candidate-0001",
        sized_candidate=_candidate(1, size_bytes=12),
        stratum=1,
        stratum_population=20,
    )

    request = markdown_batch.batch_request(
        sampled,
        content="# Rules\n\nUse snake_case.\n",
        model="gpt-5.6-luna",
        reasoning_effort="medium",
    )

    body = cast(dict[str, object], request["body"])
    prompt_path = Path(__file__).resolve().parents[1] / "prompts" / "markdown_file_classification.md"
    assert body["instructions"] == prompt_path.read_text(encoding="utf-8")
    text = cast(dict[str, object], body["text"])
    output_format = cast(dict[str, object], text["format"])
    schema = cast(dict[str, object], output_format["schema"])
    schema_path = Path(__file__).resolve().parents[1] / "prompts" / "markdown_file_classification_schema.json"
    assert schema == json.loads(schema_path.read_text(encoding="utf-8"))


def test_load_candidates_rejects_duplicate_repository_revision_paths(tmp_path: Path) -> None:
    candidate_csv = tmp_path / "candidates.csv"
    candidate_csv.write_text(
        "name,lastCommitSHA,markdown_path,markdown_url,matched_filename_terms\n"
        "example/project,0123456789abcdef,README.md,https://example.test/README.md,readme\n"
        "example/project,0123456789abcdef,README.md,https://example.test/README.md,readme\n",
        encoding="utf-8",
    )

    try:
        markdown_batch.load_candidates(candidate_csv)
    except ValueError as error:
        assert "duplicate Markdown candidate" in str(error)
    else:
        raise AssertionError("duplicate candidates must be rejected")


def test_size_candidates_reads_each_repository_tree_once(mocker: MockerFixture) -> None:
    candidates = (
        _candidate(0, size_bytes=10).candidate,
        markdown_batch.MarkdownCandidate(
            repository="example/project-0",
            revision="0000000000000000000000000000000000000001",
            path="docs/README-3.md",
            url="https://example.test/docs/README-3.md",
            matched_terms=("readme",),
        ),
    )
    client = mocker.Mock()
    client.get_complete_tree.return_value = github_client.RepositoryTree(
        entries=(
            github_client.TreeEntry(path=candidates[0].path, sha="first", size=10),
            github_client.TreeEntry(path=candidates[1].path, sha="second", size=30),
        ),
        truncated=False,
    )

    sized = markdown_batch.size_candidates(client, candidates)

    assert [item.size_bytes for item in sized] == [10, 30]
    client.get_complete_tree.assert_called_once_with(candidates[0].repository, candidates[0].revision)


def test_prepare_cost_pilot_writes_twenty_unique_batch_requests(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    candidate_csv = tmp_path / "candidates.csv"
    with candidate_csv.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(
            output_file,
            fieldnames=(
                "name",
                "lastCommitSHA",
                "markdown_path",
                "markdown_url",
                "matched_filename_terms",
            ),
        )
        writer.writeheader()
        for index in range(100):
            writer.writerow(
                {
                    "name": "example/project",
                    "lastCommitSHA": "0123456789abcdef",
                    "markdown_path": f"docs/README-{index}.md",
                    "markdown_url": f"https://example.test/docs/README-{index}.md",
                    "matched_filename_terms": "readme",
                },
            )
    client = mocker.Mock()
    client.get_complete_tree.return_value = github_client.RepositoryTree(
        entries=tuple(
            github_client.TreeEntry(path=f"docs/README-{index}.md", sha=str(index), size=index + 1)
            for index in range(100)
        ),
        truncated=False,
    )
    client.get_text_file.side_effect = lambda _repository, _revision, path: f"# {path}\n"
    output_dir = tmp_path / "output"

    report = markdown_batch.prepare_cost_pilot(
        candidate_csv=candidate_csv,
        output_dir=output_dir,
        client=client,
        sample_size=20,
        sample_seed=20260806,
        model="gpt-5.6-luna",
        reasoning_effort="medium",
        max_output_tokens=2_000,
        workers=4,
    )

    lines = (output_dir / "batch_input.jsonl").read_text(encoding="utf-8").splitlines()
    requests = [json.loads(line) for line in lines]
    assert report.sampled == 20
    assert len(requests) == 20
    assert len({request["custom_id"] for request in requests}) == 20
    assert {request["body"]["max_output_tokens"] for request in requests} == {2_000}
    assert (output_dir / "sample_manifest.csv").exists()
    configuration = json.loads((output_dir / "run_configuration.json").read_text(encoding="utf-8"))
    assert configuration["model"] == "gpt-5.6-luna"
    assert configuration["max_output_tokens"] == 2_000
    assert configuration["workers"] == 4
    assert configuration["prompt_version"] == "code-test-rule"


def test_prepare_cost_pilot_can_prepare_all_candidates(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    candidate_csv = tmp_path / "candidates.csv"
    with candidate_csv.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(
            output_file,
            fieldnames=(
                "name",
                "lastCommitSHA",
                "markdown_path",
                "markdown_url",
                "matched_filename_terms",
            ),
        )
        writer.writeheader()
        for index in range(3):
            writer.writerow(
                {
                    "name": "example/project",
                    "lastCommitSHA": "0123456789abcdef",
                    "markdown_path": f"README-{index}.md",
                    "markdown_url": f"https://example.test/README-{index}.md",
                    "matched_filename_terms": "readme",
                },
            )
    client = mocker.Mock()
    client.get_complete_tree.return_value = github_client.RepositoryTree(
        entries=tuple(
            github_client.TreeEntry(path=f"README-{index}.md", sha=str(index), size=index + 1) for index in range(3)
        ),
        truncated=False,
    )
    client.get_text_file.side_effect = lambda _repository, _revision, path: f"# {path}\n"
    output_dir = tmp_path / "output"

    report = markdown_batch.prepare_cost_pilot(
        candidate_csv=candidate_csv,
        output_dir=output_dir,
        client=client,
        sample_size=None,
        sample_seed=20260807,
        model="gpt-5.6-luna",
        reasoning_effort="max",
        workers=2,
    )

    configuration = json.loads((output_dir / "run_configuration.json").read_text(encoding="utf-8"))
    assert report.sampled == 3
    assert configuration["selection_mode"] == "all_candidates"
    assert configuration["sample_size"] is None


def test_submit_cost_pilot_uploads_once_and_records_the_batch(mocker: MockerFixture, tmp_path: Path) -> None:
    output_dir = tmp_path / "pilot"
    output_dir.mkdir()
    (output_dir / "batch_input.jsonl").write_text("{}\n", encoding="utf-8")
    client = mocker.Mock()
    client.upload_input.return_value = {"id": "file-input"}
    client.create_batch.return_value = {"id": "batch-1", "status": "validating"}

    submission = markdown_batch.submit_cost_pilot(output_dir=output_dir, client=client)

    assert submission["batch_id"] == "batch-1"
    client.upload_input.assert_called_once_with(filename="batch_input.jsonl", content=b"{}\n")
    client.create_batch.assert_called_once_with(input_file_id="file-input")
    assert json.loads((output_dir / "batch_submission.json").read_text(encoding="utf-8"))["batch_id"] == "batch-1"

    try:
        markdown_batch.submit_cost_pilot(output_dir=output_dir, client=client)
    except FileExistsError as error:
        assert "already submitted" in str(error)
    else:
        raise AssertionError("a prepared pilot must not be submitted twice")


def test_collect_cost_pilot_verifies_quotes_and_uses_custom_ids(mocker: MockerFixture, tmp_path: Path) -> None:
    output_dir = tmp_path / "pilot"
    output_dir.mkdir()
    _write_collection_fixture(output_dir)
    output_lines = (
        _batch_output_line(
            "candidate-0002",
            {
                "label": "YES",
                "reason": "The document states a source-code rule.",
                "quote": "missing quote",
                "confidence": 7,
            },
            input_tokens=200,
            output_tokens=20,
        ),
        _batch_output_line(
            "candidate-0001",
            {
                "label": "YES",
                "reason": "The document requires snake_case.",
                "quote": "Use snake_case.",
                "confidence": 9,
            },
            input_tokens=100,
            output_tokens=10,
        ),
    )
    client = mocker.Mock()
    client.retrieve_batch.return_value = {
        "id": "batch-1",
        "status": "completed",
        "output_file_id": "file-output",
        "error_file_id": "file-errors",
    }
    client.download_file.side_effect = (
        "".join(f"{json.dumps(line)}\n" for line in output_lines).encode(),
        b'{"custom_id":"candidate-0003","response":null,"error":{"code":"batch_expired"}}\n',
    )

    report = markdown_batch.collect_cost_pilot(output_dir=output_dir, client=client)

    rows = list(csv.DictReader((output_dir / "classified_files.csv").open(encoding="utf-8", newline="")))
    assert [row["custom_id"] for row in rows] == ["candidate-0001", "candidate-0002", "candidate-0003"]
    assert [row["status"] for row in rows] == ["pass", "review", "model_error"]
    assert rows[0]["model_label"] == "YES"
    assert rows[0]["model_reason"] == "The document requires snake_case."
    assert rows[0]["confidence"] == "9"
    assert rows[1]["reason"] == "yes_without_quote"
    assert report["completed"] == 2
    assert report["errors"] == 1
    assert cast(float, report["pilot_cost_usd"]) == 0.000048
    assert report["estimated_full_batch_usd"] is None
    assert (output_dir / "batch_output.jsonl").exists()
    assert (output_dir / "batch_errors.jsonl").exists()


def test_collect_precomputed_cost_pilot_uses_per_response_provider_cost(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "pilot"
    output_dir.mkdir()
    _write_collection_fixture(output_dir)
    output = _batch_output_line(
        "candidate-0001",
        {
            "label": "YES",
            "reason": "The document requires snake_case.",
            "quote": "Use snake_case.",
            "confidence": 9,
        },
        input_tokens=100,
        output_tokens=10,
        cost_usd=0.000012,
    )

    report = markdown_batch.collect_precomputed_cost_pilot(
        output_dir=output_dir,
        output_content=f"{json.dumps(output)}\n".encode(),
        error_content=b"",
    )

    rows = list(csv.DictReader((output_dir / "classified_files.csv").open(encoding="utf-8", newline="")))
    assert rows[0]["cost_usd"] == "1.2e-05"
    assert report["provider_reported_cost_usd"] == 0.000012
    assert report["pilot_cost_usd"] == 0.000012


def test_collect_precomputed_bedrock_cost_uses_long_context_rates(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "pilot"
    output_dir.mkdir()
    _write_collection_fixture(output_dir)
    output = _batch_output_line(
        "candidate-0001",
        {
            "label": "NO",
            "reason": "The document contains no rule.",
            "quote": "",
            "confidence": 9,
        },
        input_tokens=272_001,
        output_tokens=100,
    )

    report = markdown_batch.collect_precomputed_cost_pilot(
        output_dir=output_dir,
        output_content=f"{json.dumps(output)}\n".encode(),
        error_content=b"",
        provider="bedrock",
    )

    rows = list(csv.DictReader((output_dir / "classified_files.csv").open(encoding="utf-8", newline="")))
    assert rows[0]["cost_usd"] == "0.11987844"
    assert report["long_context_requests"] == 1
    assert report["short_context_requests"] == 0


def test_collect_cost_pilot_prices_cache_reads_and_writes_separately(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "pilot"
    output_dir.mkdir()
    _write_collection_fixture(output_dir)
    output = _batch_output_line(
        "candidate-0001",
        {
            "label": "YES",
            "reason": "The document requires snake_case.",
            "quote": "Use snake_case.",
            "confidence": 9,
        },
        input_tokens=4_000,
        cached_input_tokens=1_920,
        cache_write_input_tokens=1_024,
        output_tokens=300,
    )
    client = mocker.Mock()
    client.retrieve_batch.return_value = {
        "id": "batch-1",
        "status": "completed",
        "output_file_id": "file-output",
    }
    client.download_file.return_value = f"{json.dumps(output)}\n".encode()

    report = markdown_batch.collect_cost_pilot(output_dir=output_dir, client=client)

    rows = list(csv.DictReader((output_dir / "classified_files.csv").open(encoding="utf-8", newline="")))
    assert rows[0]["uncached_input_tokens"] == "1056"
    assert rows[0]["cached_input_tokens"] == "1920"
    assert rows[0]["cache_write_input_tokens"] == "1024"
    assert rows[0]["cost_usd"] == "0.0004328"
    assert cast(float, report["pilot_cost_usd"]) == 0.000433


def test_collect_cost_pilot_maps_no_with_an_empty_quote_to_not_found(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "pilot"
    output_dir.mkdir()
    _write_collection_fixture(output_dir)
    output = _batch_output_line(
        "candidate-0001",
        {
            "label": "NO",
            "reason": "The document only explains product usage.",
            "quote": "",
            "confidence": 8,
        },
        input_tokens=100,
        output_tokens=10,
    )
    client = mocker.Mock()
    client.retrieve_batch.return_value = {
        "id": "batch-1",
        "status": "completed",
        "output_file_id": "file-output",
    }
    client.download_file.return_value = f"{json.dumps(output)}\n".encode()

    markdown_batch.collect_cost_pilot(output_dir=output_dir, client=client)

    rows = list(csv.DictReader((output_dir / "classified_files.csv").open(encoding="utf-8", newline="")))
    assert rows[0]["status"] == "not_found"
    assert rows[0]["model_label"] == "NO"
    assert rows[0]["model_reason"] == "The document only explains product usage."
    assert rows[0]["quote"] == ""
    assert rows[0]["confidence"] == "8"


def test_collect_cost_pilot_identifies_yes_without_an_exact_quote(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "pilot"
    output_dir.mkdir()
    _write_collection_fixture(output_dir)
    output = _batch_output_line(
        "candidate-0001",
        {
            "label": "YES",
            "reason": "The document contains a source-code rule.",
            "quote": "",
            "confidence": 7,
        },
        input_tokens=100,
        output_tokens=10,
    )
    client = mocker.Mock()
    client.retrieve_batch.return_value = {
        "id": "batch-1",
        "status": "completed",
        "output_file_id": "file-output",
    }
    client.download_file.return_value = f"{json.dumps(output)}\n".encode()

    markdown_batch.collect_cost_pilot(output_dir=output_dir, client=client)

    rows = list(csv.DictReader((output_dir / "classified_files.csv").open(encoding="utf-8", newline="")))
    assert rows[0]["status"] == "review"
    assert rows[0]["reason"] == "yes_without_quote"


def test_collect_cost_pilot_identifies_no_with_a_quote(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "pilot"
    output_dir.mkdir()
    _write_collection_fixture(output_dir)
    output = _batch_output_line(
        "candidate-0001",
        {
            "label": "NO",
            "reason": "The document contains no source-code rule.",
            "quote": "Use snake_case.",
            "confidence": 7,
        },
        input_tokens=100,
        output_tokens=10,
    )
    client = mocker.Mock()
    client.retrieve_batch.return_value = {
        "id": "batch-1",
        "status": "completed",
        "output_file_id": "file-output",
    }
    client.download_file.return_value = f"{json.dumps(output)}\n".encode()

    markdown_batch.collect_cost_pilot(output_dir=output_dir, client=client)

    rows = list(csv.DictReader((output_dir / "classified_files.csv").open(encoding="utf-8", newline="")))
    assert rows[0]["status"] == "review"
    assert rows[0]["reason"] == "no_with_quote"


def test_parse_json_response_accepts_a_downloaded_responses_document() -> None:
    document = _responses_document(
        {
            "label": "NO",
            "reason": "The document only explains product usage.",
            "quote": "",
            "confidence": 8,
        },
        input_tokens=100,
        output_tokens=10,
    )

    response = openai_responses_client.parse_json_response(document)

    assert response.value == {
        "label": "NO",
        "reason": "The document only explains product usage.",
        "quote": "",
        "confidence": 8,
    }
    assert response.usage.total_tokens == 110


def _write_collection_fixture(output_dir: Path) -> None:
    (output_dir / "batch_submission.json").write_text(
        json.dumps({"batch_id": "batch-1"}),
        encoding="utf-8",
    )
    manifest_rows = (
        ("candidate-0001", "1", "10", "README.md"),
        ("candidate-0002", "2", "10", "docs/GUIDE.md"),
        ("candidate-0003", "3", "10", "AGENTS.md"),
    )
    with (output_dir / "sample_manifest.csv").open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.writer(output_file)
        writer.writerow(
            (
                "custom_id",
                "stratum",
                "stratum_population",
                "name",
                "lastCommitSHA",
                "markdown_path",
                "markdown_url",
                "matched_filename_terms",
                "size_bytes",
            ),
        )
        for custom_id, stratum, population, path in manifest_rows:
            writer.writerow(
                (
                    custom_id,
                    stratum,
                    population,
                    "example/project",
                    "0123456789abcdef",
                    path,
                    f"https://example.test/{path}",
                    "readme",
                    "100",
                ),
            )
    contents = {
        "candidate-0001": "# Rules\n\nUse snake_case.\n",
        "candidate-0002": "# Guide\n\nRun the application.\n",
        "candidate-0003": "# Agents\n\nWrite tests.\n",
    }
    requests = []
    for custom_id, content in contents.items():
        requests.append(
            {
                "custom_id": custom_id,
                "method": "POST",
                "url": "/v1/responses",
                "body": {"input": json.dumps({"content": content})},
            },
        )
    (output_dir / "batch_input.jsonl").write_text(
        "".join(f"{json.dumps(request)}\n" for request in requests),
        encoding="utf-8",
    )


def _batch_output_line(
    custom_id: str,
    value: dict[str, object],
    *,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int = 0,
    cache_write_input_tokens: int = 0,
    cost_usd: float | None = None,
) -> dict[str, object]:
    return {
        "custom_id": custom_id,
        "response": {
            "status_code": 200,
            "body": _responses_document(
                value,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_input_tokens=cached_input_tokens,
                cache_write_input_tokens=cache_write_input_tokens,
                cost_usd=cost_usd,
            ),
        },
        "error": None,
    }


def _responses_document(
    value: dict[str, object],
    *,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int = 0,
    cache_write_input_tokens: int = 0,
    cost_usd: float | None = None,
) -> dict[str, object]:
    document: dict[str, object] = {
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": json.dumps(value)}],
            },
        ],
        "usage": {
            "input_tokens": input_tokens,
            "input_tokens_details": {
                "cached_tokens": cached_input_tokens,
                "cache_write_tokens": cache_write_input_tokens,
            },
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
    }
    if cost_usd is not None:
        cast(dict[str, object], document["usage"])["cost"] = cost_usd
    return document
