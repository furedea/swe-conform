import json
import os
from pathlib import Path
from subprocess import CompletedProcess

import pytest
from pytest_mock import MockerFixture

import batch_runner
import cache_runner
import codex_cli_client
import main
import markdown_audit
import markdown_batch
import markdown_evaluation
import markdown_filename_audit
import markdown_full_review
import markdown_review
import repository_sampling
import repository_workspace


def test_main() -> None:
    main.main([])


def test_command_is_named_swe_conform() -> None:
    assert main._parser().prog == "swe-conform"


def test_codex_cli_provider_defaults_to_max_reasoning_effort() -> None:
    assert main.effective_reasoning_effort(provider="codex-cli", configured=None) == "max"


def test_filter_defaults_to_four_concurrent_codex_processes() -> None:
    arguments = main._parser().parse_args(["filter"])

    assert arguments.workers == 4


def test_sample_repositories_defaults_to_a_fifty_repository_held_out_sample() -> None:
    arguments = main._parser().parse_args(
        [
            "sample-repositories",
            "--output-dir",
            "experiments/heldout",
            "--exclude-csv",
            "experiments/calibration/input/candidates.csv",
        ],
    )

    assert arguments.input_dir == Path("docs/repository-candidates")
    assert arguments.sample_size == 50
    assert arguments.sample_seed == 20260807
    assert arguments.exclude_csv == [Path("experiments/calibration/input/candidates.csv")]


def test_batch_markdown_prepare_defaults_to_a_twenty_file_luna_pilot() -> None:
    arguments = main._parser().parse_args(
        [
            "batch-markdown",
            "prepare",
            "--candidate-csv",
            "experiments/audit/markdown_filename_files.csv",
            "--output-dir",
            "experiments/pilot",
        ],
    )

    assert arguments.sample_size == 20
    assert arguments.sample_seed == 20260806
    assert arguments.model == "gpt-5.6-luna"
    assert arguments.reasoning_effort == "medium"
    assert arguments.workers == 4


def test_classify_markdown_prepare_uses_the_fixed_project_rule_settings() -> None:
    arguments = main._parser().parse_args(
        [
            "classify-markdown",
            "prepare",
            "--candidate-csv",
            "experiments/audit/markdown_filename_files.csv",
            "--output-dir",
            "experiments/pilot",
        ],
    )

    assert arguments.model == "gpt-5.6-luna"
    assert arguments.reasoning_effort == "max"
    assert arguments.max_output_tokens == 32_000
    assert arguments.sample_size == 20
    assert arguments.workers == 16
    assert arguments.provider == "bedrock"
    assert arguments.bedrock_region == "us-east-1"


def test_classify_markdown_prepare_can_select_all_filtered_files() -> None:
    arguments = main._parser().parse_args(
        [
            "classify-markdown",
            "prepare",
            "--candidate-csv",
            "experiments/audit/markdown_filename_files.csv",
            "--output-dir",
            "experiments/judge",
            "--all-candidates",
            "--max-output-tokens",
            "4096",
        ],
    )

    assert arguments.all_candidates is True
    assert arguments.max_output_tokens == 4_096


def test_batch_markdown_prepare_builds_revision_pinned_requests(
    mocker: MockerFixture,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    credential = mocker.patch("main.github_credential", autospec=True, return_value="github-credential")
    client = mocker.patch("main.github_client.GitHubClient", autospec=True)
    prepare = mocker.patch(
        "main.markdown_batch.prepare_cost_pilot",
        autospec=True,
        return_value=markdown_batch.MarkdownBatchPreparation(candidates=2524, sampled=20, output_dir=tmp_path),
    )

    main.main(
        [
            "batch-markdown",
            "prepare",
            "--candidate-csv",
            "experiments/audit/markdown_filename_files.csv",
            "--output-dir",
            str(tmp_path),
        ],
    )

    credential.assert_called_once_with()
    client.assert_called_once_with(token="github-credential")
    prepare.assert_called_once_with(
        candidate_csv=Path("experiments/audit/markdown_filename_files.csv"),
        output_dir=tmp_path,
        client=client.return_value,
        sample_size=20,
        sample_seed=20260806,
        model="gpt-5.6-luna",
        reasoning_effort="medium",
        max_output_tokens=16_000,
        workers=4,
    )
    client.return_value.close.assert_called_once_with()
    assert json.loads(capsys.readouterr().out)["sampled"] == 20


def test_batch_markdown_submit_uses_the_openai_api_key(
    mocker: MockerFixture,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    mocker.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"})
    client = mocker.patch("main.openai_batch_client.OpenAIBatchClient", autospec=True)
    submit = mocker.patch(
        "main.markdown_batch.submit_cost_pilot",
        autospec=True,
        return_value={"batch_id": "batch-1", "status": "validating"},
    )

    main.main(["batch-markdown", "submit", "--output-dir", str(tmp_path)])

    client.assert_called_once_with(api_key="sk-test")
    submit.assert_called_once_with(output_dir=tmp_path, client=client.return_value)
    client.return_value.close.assert_called_once_with()
    assert json.loads(capsys.readouterr().out)["batch_id"] == "batch-1"


def test_batch_markdown_rejects_openrouter_as_a_batch_provider() -> None:
    with pytest.raises(SystemExit):
        main._parser().parse_args(
            [
                "batch-markdown",
                "submit",
                "--output-dir",
                "experiments/pilot",
                "--provider",
                "openrouter",
            ],
        )


def test_classify_markdown_run_uses_the_fixed_project_rule_settings_by_default() -> None:
    arguments = main._parser().parse_args(
        [
            "classify-markdown",
            "run",
            "--output-dir",
            "experiments/pilot",
        ],
    )

    assert arguments.provider == "bedrock"
    assert arguments.bedrock_region == "us-east-1"
    assert arguments.workers == 16


def test_classify_markdown_run_rejects_a_region_without_luna() -> None:
    with pytest.raises(SystemExit):
        main._parser().parse_args(
            [
                "classify-markdown",
                "run",
                "--output-dir",
                "experiments/pilot",
                "--provider",
                "bedrock",
                "--bedrock-region",
                "ap-northeast-1",
            ],
        )


def test_classify_markdown_run_uses_openrouter_responses_concurrently(
    mocker: MockerFixture,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    mocker.patch.dict(os.environ, {"OPENROUTER_API_KEY": __name__})
    client = mocker.patch("main.openrouter_responses_client.OpenRouterResponsesClient", autospec=True)
    run = mocker.patch(
        "main.markdown_responses_runner.run_prepared_classification",
        autospec=True,
        return_value={"requested": 20, "completed": 20, "provider_reported_cost_usd": 0.01},
    )

    main.main(
        [
            "classify-markdown",
            "run",
            "--output-dir",
            str(tmp_path),
            "--provider",
            "openrouter",
            "--workers",
            "4",
        ],
    )

    client.assert_called_once_with(api_key=__name__)
    run.assert_called_once_with(
        output_dir=tmp_path,
        client=client.return_value,
        provider="openrouter",
        region=None,
        workers=4,
    )
    client.return_value.close.assert_called_once_with()
    assert json.loads(capsys.readouterr().out)["completed"] == 20


def test_classify_markdown_run_uses_bedrock_responses_concurrently(
    mocker: MockerFixture,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    mocker.patch.dict(os.environ, {"AWS_BEARER_TOKEN_BEDROCK": __name__})
    client = mocker.patch("main.bedrock_responses_client.BedrockResponsesClient", autospec=True)
    run = mocker.patch(
        "main.markdown_responses_runner.run_prepared_classification",
        autospec=True,
        return_value={"requested": 20, "completed": 20, "provider_reported_cost_usd": None},
    )

    main.main(
        [
            "classify-markdown",
            "run",
            "--output-dir",
            str(tmp_path),
        ],
    )

    client.assert_called_once_with(api_key=__name__, region="us-east-1")
    run.assert_called_once_with(
        output_dir=tmp_path,
        client=client.return_value,
        provider="bedrock",
        region="us-east-1",
        workers=16,
    )
    client.return_value.close.assert_called_once_with()
    assert json.loads(capsys.readouterr().out)["completed"] == 20


def test_classify_markdown_export_pass_materializes_manual_review_files(
    mocker: MockerFixture,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "file-judge"
    review_dir = tmp_path / "pass-review"
    export = mocker.patch(
        "main.markdown_review.export_pass_files",
        autospec=True,
        return_value=markdown_review.MarkdownReviewReport(files=64, output_dir=review_dir),
    )

    main.main(
        [
            "classify-markdown",
            "export-pass",
            "--output-dir",
            str(run_dir),
            "--review-dir",
            str(review_dir),
        ],
    )

    export.assert_called_once_with(
        classified_files_path=run_dir / "classified_files.csv",
        batch_input_path=run_dir / "batch_input.jsonl",
        output_dir=review_dir,
    )
    assert json.loads(capsys.readouterr().out)["files"] == 64


def test_classify_markdown_export_candidates_materializes_blind_review_files(
    mocker: MockerFixture,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    candidate_csv = tmp_path / "candidates.csv"
    run_dir = tmp_path / "file-judge"
    review_dir = tmp_path / "manual-review"
    export = mocker.patch(
        "main.markdown_review.export_candidate_files",
        autospec=True,
        return_value=markdown_review.MarkdownReviewReport(files=127, output_dir=review_dir),
    )

    main.main(
        [
            "classify-markdown",
            "export-candidates",
            "--candidate-csv",
            str(candidate_csv),
            "--output-dir",
            str(run_dir),
            "--review-dir",
            str(review_dir),
        ],
    )

    export.assert_called_once_with(
        candidate_csv=candidate_csv,
        batch_input_path=run_dir / "batch_input.jsonl",
        output_dir=review_dir,
    )
    assert json.loads(capsys.readouterr().out)["files"] == 127


def test_classify_markdown_evaluate_compares_human_decisions(
    mocker: MockerFixture,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "file-judge"
    checklist_path = tmp_path / "checklist.csv"
    repository_csv_path = tmp_path / "repositories.csv"
    evaluation_dir = run_dir / "evaluation"
    evaluate = mocker.patch(
        "main.markdown_evaluation.evaluate_classifications",
        autospec=True,
        return_value=markdown_evaluation.ClassificationEvaluation(
            true_positives=20,
            false_positives=3,
            false_negatives=2,
            true_negatives=100,
            review_decisions=4,
            model_errors=1,
            missing_predictions=0,
            checklist_rows=130,
            human_labeled_files=130,
            input_repositories=50,
            human_labeled_repositories=50,
            human_pass_repositories=30,
            llm_pass_repositories=28,
            output_dir=evaluation_dir,
        ),
    )

    main.main(
        [
            "classify-markdown",
            "evaluate",
            "--output-dir",
            str(run_dir),
            "--checklist-csv",
            str(checklist_path),
            "--repository-csv",
            str(repository_csv_path),
        ],
    )

    evaluate.assert_called_once_with(
        classified_files_path=run_dir / "classified_files.csv",
        checklist_path=checklist_path,
        repository_csv_path=repository_csv_path,
        output_dir=evaluation_dir,
    )
    output = json.loads(capsys.readouterr().out)
    assert output["false_positives"] == 3
    assert output["input_repositories"] == 50
    assert output["human_labeled_repositories"] == 50
    assert output["human_pass_repositories"] == 30
    assert output["llm_pass_repositories"] == 28
    assert output["resolved_accuracy"] == 0.96
    assert output["evaluation_dir"] == str(evaluation_dir)


def test_classify_markdown_codex_review_builds_a_resumable_full_checklist(
    mocker: MockerFixture,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    candidate_csv = tmp_path / "candidates.csv"
    classified_csv = tmp_path / "classified.csv"
    batch_input = tmp_path / "batch_input.jsonl"
    existing_checklist = tmp_path / "checklist.csv"
    checkpoint = tmp_path / "checkpoint.jsonl"
    output_csv = tmp_path / "checklist_full.csv"
    prompt_path = tmp_path / "prompt.md"
    client = mocker.patch("main.codex_cli_client.CodexCliClient", autospec=True)
    build = mocker.patch(
        "main.markdown_full_review.build_full_checklist",
        autospec=True,
        return_value=markdown_full_review.FullChecklistReport(
            existing=166,
            codex_added=561,
            reviewed=561,
            remaining=0,
            rows=727,
            output_path=output_csv,
            output_written=True,
        ),
    )

    main.main(
        [
            "classify-markdown",
            "codex-review",
            "--candidate-csv",
            str(candidate_csv),
            "--classified-files",
            str(classified_csv),
            "--batch-input",
            str(batch_input),
            "--existing-checklist",
            str(existing_checklist),
            "--checkpoint-jsonl",
            str(checkpoint),
            "--output-csv",
            str(output_csv),
            "--prompt-path",
            str(prompt_path),
            "--workers",
            "4",
        ],
    )

    client.assert_called_once_with(command="codex", timeout_seconds=1_800)
    build.assert_called_once_with(
        candidate_csv=candidate_csv,
        classified_files_path=classified_csv,
        batch_input_path=batch_input,
        existing_checklist_path=existing_checklist,
        checkpoint_path=checkpoint,
        output_path=output_csv,
        prompt_path=prompt_path,
        client=client.return_value,
        model="gpt-5.6-sol",
        reasoning_effort="max",
        workers=4,
        max_batches=None,
    )
    client.return_value.close.assert_called_once_with()
    output = json.loads(capsys.readouterr().out)
    assert output["existing"] == 166
    assert output["codex_added"] == 561
    assert output["rows"] == 727


def test_markdown_audit_accepts_repeatable_agent_evidence_reports() -> None:
    arguments = main._parser().parse_args(
        [
            "audit-markdown",
            "--input-dir",
            "experiments/input",
            "--output-dir",
            "experiments/output",
            "--evidence-csv",
            "first/guideline_files.csv",
            "--evidence-csv",
            "retry/guideline_files.csv",
        ],
    )

    assert arguments.evidence_csv == [
        Path("first/guideline_files.csv"),
        Path("retry/guideline_files.csv"),
    ]
    assert arguments.workers == 4
    assert not hasattr(arguments, "checkout_timeout_seconds")


def test_markdown_filename_audit_accepts_a_local_git_cache() -> None:
    arguments = main._parser().parse_args(
        [
            "audit-markdown-filenames",
            "--input-dir",
            "experiments/input",
            "--output-dir",
            "experiments/output",
            "--evidence-csv",
            "experiment/guideline_files.csv",
            "--cache-root",
            "/mnt/hdd/repositories",
            "--git-command",
            "/usr/local/bin/git",
        ],
    )

    assert arguments.cache_root == Path("/mnt/hdd/repositories")
    assert arguments.git_command == "/usr/local/bin/git"


def test_markdown_filename_audit_does_not_require_prior_agent_evidence() -> None:
    arguments = main._parser().parse_args(
        [
            "audit-markdown-filenames",
            "--input-dir",
            "experiments/input",
            "--output-dir",
            "experiments/output",
        ],
    )

    assert arguments.evidence_csv is None


def test_sample_repositories_writes_the_reproducible_input(
    mocker: MockerFixture,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    write_sample = mocker.patch(
        "main.repository_sampling.write_stratified_sample",
        autospec=True,
        return_value=repository_sampling.RepositorySamplingReport(
            population=4359,
            excluded=20,
            eligible=4339,
            sampled=50,
            language_populations={"Java": 653},
            language_sample_sizes={"Java": 13},
            output_dir=tmp_path,
        ),
    )

    main.main(
        [
            "sample-repositories",
            "--input-dir",
            "docs/repository-candidates",
            "--output-dir",
            str(tmp_path),
            "--exclude-csv",
            "experiments/calibration/input/candidates.csv",
        ],
    )

    write_sample.assert_called_once_with(
        input_dir=Path("docs/repository-candidates"),
        output_dir=tmp_path,
        sample_size=50,
        sample_seed=20260807,
        exclude_csvs=(Path("experiments/calibration/input/candidates.csv"),),
    )
    assert json.loads(capsys.readouterr().out)["sampled"] == 50


def test_markdown_filename_audit_prefers_the_configured_local_git_cache(
    mocker: MockerFixture,
) -> None:
    arguments = main._parser().parse_args(
        [
            "audit-markdown-filenames",
            "--input-dir",
            "experiments/input",
            "--output-dir",
            "experiments/output",
            "--evidence-csv",
            "experiment/guideline_files.csv",
            "--cache-root",
            "/mnt/hdd/repositories",
        ],
    )
    github = mocker.Mock()
    cache = mocker.patch("main.repository_cache.GitRepositoryCache", autospec=True)
    client = mocker.patch("main.repository_tree.CachedRepositoryTreeClient", autospec=True)

    selected = main._markdown_filename_tree_client(arguments, fallback=github)

    cache.assert_called_once_with(root=Path("/mnt/hdd/repositories"), command="git")
    client.assert_called_once_with(cache=cache.return_value, fallback=github, command="git")
    assert selected is client.return_value


def test_markdown_audit_writes_the_repository_term_coverage_reports(
    mocker: MockerFixture,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    load_candidates = mocker.patch("main.repository.load_repository_candidates", autospec=True, return_value=())
    load_evidence = mocker.patch("main.markdown_audit.load_agent_evidence", autospec=True, return_value={})
    credential = mocker.patch("main.github_credential", autospec=True, return_value="github-credential")
    client = mocker.patch("main.github_client.GitHubClient", autospec=True)
    auditor = mocker.patch("main.markdown_audit.MarkdownAuditor", autospec=True)
    runner = mocker.patch("main.markdown_audit.MarkdownAuditRunner", autospec=True)
    report = markdown_audit.MarkdownAuditReport(
        results=(),
        stats=markdown_audit.MarkdownAuditStats(
            requested=0,
            completed=0,
            errors=0,
            elapsed_seconds=1.25,
        ),
    )
    runner.return_value.run.return_value = report
    write_reports = mocker.patch("main.markdown_audit.write_reports", autospec=True)
    output_dir = tmp_path / "output"

    main.main(
        [
            "audit-markdown",
            "--input-dir",
            "experiments/input",
            "--output-dir",
            str(output_dir),
            "--evidence-csv",
            "experiment/guideline_files.csv",
            "--allow-out-of-window-snapshots",
        ],
    )

    load_candidates.assert_called_once_with(Path("experiments/input"), enforce_snapshot_window=False)
    load_evidence.assert_called_once_with([Path("experiment/guideline_files.csv")])
    credential.assert_called_once_with()
    client.assert_called_once_with(token="github-credential")
    auditor.assert_called_once_with(client=client.return_value, agent_evidence={})
    runner.assert_called_once_with(auditor=auditor.return_value, workers=4)
    runner.return_value.run.assert_called_once_with((), limit=None)
    client.return_value.close.assert_called_once_with()
    write_reports.assert_called_once_with(report, output_dir)
    assert json.loads(capsys.readouterr().out) == {
        "completed": 0,
        "elapsed_seconds": 1.25,
        "errors": 0,
        "output_dir": str(output_dir),
        "requested": 0,
    }


def test_markdown_filename_audit_writes_independent_coverage_reports(
    mocker: MockerFixture,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    load_candidates = mocker.patch("main.repository.load_repository_candidates", autospec=True, return_value=())
    load_evidence = mocker.patch("main.markdown_audit.load_agent_evidence", autospec=True, return_value={})
    mocker.patch("main.github_credential", autospec=True, return_value="github-credential")
    client = mocker.patch("main.github_client.GitHubClient", autospec=True)
    auditor = mocker.patch("main.markdown_filename_audit.MarkdownFilenameAuditor", autospec=True)
    runner = mocker.patch("main.markdown_filename_audit.MarkdownFilenameAuditRunner", autospec=True)
    report = markdown_filename_audit.MarkdownFilenameAuditReport(
        results=(),
        stats=markdown_filename_audit.MarkdownFilenameAuditStats(
            requested=0,
            completed=0,
            errors=0,
            elapsed_seconds=1.25,
        ),
    )
    runner.return_value.run.return_value = report
    write_reports = mocker.patch("main.markdown_filename_audit.write_reports", autospec=True)
    output_dir = tmp_path / "output"

    main.main(
        [
            "audit-markdown-filenames",
            "--input-dir",
            "experiments/input",
            "--output-dir",
            str(output_dir),
            "--evidence-csv",
            "experiment/guideline_files.csv",
            "--allow-out-of-window-snapshots",
        ],
    )

    load_candidates.assert_called_once_with(Path("experiments/input"), enforce_snapshot_window=False)
    load_evidence.assert_called_once_with([Path("experiment/guideline_files.csv")])
    auditor.assert_called_once_with(client=client.return_value, agent_evidence={})
    runner.assert_called_once_with(auditor=auditor.return_value, workers=4)
    runner.return_value.run.assert_called_once_with((), limit=None)
    client.return_value.close.assert_called_once_with()
    write_reports.assert_called_once_with(report, output_dir)
    assert json.loads(capsys.readouterr().out)["completed"] == 0


def test_filter_runs_codex_in_the_pinned_docker_image_by_default() -> None:
    arguments = main._parser().parse_args(["filter"])

    client = main._model_client(arguments)

    assert arguments.codex_runtime == "docker"
    assert arguments.codex_image == "swe-conform-codex:0.146.0"
    assert isinstance(client, codex_cli_client.DockerCodexCliClient)


def test_docker_image_id_returns_the_local_content_digest(mocker: MockerFixture) -> None:
    run = mocker.patch(
        "main.subprocess.run",
        autospec=True,
        return_value=CompletedProcess(
            args=[],
            returncode=0,
            stdout="sha256:7ee758b81b82\n",
            stderr="",
        ),
    )

    image_id = main.docker_image_id("docker", "swe-conform-codex:0.146.0")

    assert image_id == "sha256:7ee758b81b82"
    run.assert_called_once_with(
        [
            "docker",
            "image",
            "inspect",
            "--format",
            "{{.Id}}",
            "swe-conform-codex:0.146.0",
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def test_filter_completes_sandbox_preflight_before_repository_submissions(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    mocker.patch("main.repository.load_repository_candidates", autospec=True, return_value=())
    mocker.patch("main._repository_workspace", autospec=True)
    mocker.patch("main.docker_image_id", autospec=True, return_value="sha256:image")
    mocker.patch("main.result_store.ResultStore", autospec=True)
    model_client = mocker.Mock(spec=codex_cli_client.DockerCodexCliClient)
    mocker.patch("main._model_client", autospec=True, return_value=model_client)
    mocker.patch("main.guideline_classifier.ModelGuidelineChecker", autospec=True)
    mocker.patch("main.pipeline.RepositoryFilter", autospec=True)
    runner = mocker.patch("main.batch_runner.BatchRunner", autospec=True)
    runner.return_value.run.return_value = batch_runner.RunStats(
        requested=0,
        skipped=0,
        evaluated=0,
        elapsed_seconds=0.0,
    )
    calls = mocker.Mock()
    calls.attach_mock(model_client.preflight, "preflight")
    calls.attach_mock(runner.return_value.run, "run")

    main.main(["filter", "--output-dir", str(tmp_path / "output")])

    assert [call[0] for call in calls.method_calls] == ["preflight", "run"]


def test_filter_aborts_before_submissions_when_sandbox_preflight_fails(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    mocker.patch("main.repository.load_repository_candidates", autospec=True, return_value=())
    mocker.patch("main._repository_workspace", autospec=True)
    mocker.patch("main.docker_image_id", autospec=True, return_value="sha256:image")
    mocker.patch("main.result_store.ResultStore", autospec=True)
    model_client = mocker.Mock(spec=codex_cli_client.DockerCodexCliClient)
    model_client.preflight.side_effect = codex_cli_client.CodexSandboxError("bwrap unavailable")
    mocker.patch("main._model_client", autospec=True, return_value=model_client)
    runner = mocker.patch("main.batch_runner.BatchRunner", autospec=True)

    with pytest.raises(codex_cli_client.CodexSandboxError, match="bwrap unavailable"):
        main.main(["filter", "--output-dir", str(tmp_path / "output")])

    runner.assert_not_called()
    model_client.close.assert_called_once_with()


def test_preflight_command_checks_the_configured_docker_image(
    mocker: MockerFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    mocker.patch("main.docker_image_id", autospec=True, return_value="sha256:image")
    client = mocker.patch("main.codex_cli_client.DockerCodexCliClient", autospec=True)

    main.main(["preflight"])

    client.assert_called_once_with(
        docker_command="docker",
        image="swe-conform-codex:0.146.0",
        source_codex_home=None,
    )
    client.return_value.preflight.assert_called_once_with()
    assert json.loads(capsys.readouterr().out) == {
        "codex_image": "swe-conform-codex:0.146.0",
        "codex_image_id": "sha256:image",
        "sandbox": "ready",
    }


def test_filter_defaults_to_retry_timeouts() -> None:
    arguments = main._parser().parse_args(["filter"])

    assert arguments.checkout_timeout_seconds == 900
    assert arguments.model_timeout_seconds == 1800


def test_filter_can_replay_revision_pinned_snapshots_outside_the_collection_window() -> None:
    arguments = main._parser().parse_args(["filter", "--allow-out-of-window-snapshots"])

    assert arguments.enforce_snapshot_window is False


def test_fetch_requires_hdd_cache_and_allows_one_hour_per_repository() -> None:
    arguments = main._parser().parse_args(["fetch", "--cache-root", "/mnt/hdd/repositories"])

    assert str(arguments.cache_root) == "/mnt/hdd/repositories"
    assert arguments.fetch_timeout_seconds == 3600
    assert arguments.workers == 4


def test_filter_accepts_separate_hdd_cache_and_ssd_workspace_roots() -> None:
    arguments = main._parser().parse_args(
        [
            "filter",
            "--cache-root",
            "/mnt/hdd/repositories",
            "--workspace-root",
            "/mnt/ssd/workspaces",
        ],
    )

    assert str(arguments.cache_root) == "/mnt/hdd/repositories"
    assert str(arguments.workspace_root) == "/mnt/ssd/workspaces"


def test_filter_uses_the_local_cache_when_cache_and_workspace_roots_are_given() -> None:
    arguments = main._parser().parse_args(
        [
            "filter",
            "--cache-root",
            "/mnt/hdd/repositories",
            "--workspace-root",
            "/mnt/ssd/workspaces",
        ],
    )

    workspace = main._repository_workspace(arguments)

    assert isinstance(workspace, repository_workspace.CachedGitRepositoryWorkspace)


def test_filter_keeps_the_direct_checkout_mode_for_small_pilots() -> None:
    arguments = main._parser().parse_args(["filter", "--limit", "20"])

    workspace = main._repository_workspace(arguments)

    assert isinstance(workspace, repository_workspace.GitRepositoryWorkspace)


def test_fetch_command_runs_the_resumable_cache_batch(
    mocker: MockerFixture,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    candidates = mocker.patch("main.repository.load_repository_candidates", autospec=True, return_value=())
    cache = mocker.patch("main.repository_cache.GitRepositoryCache", autospec=True)
    runner = mocker.patch("main.cache_runner.CacheBatchRunner", autospec=True)
    runner.return_value.run.return_value = cache_runner.CacheBatchReport(
        results=(),
        stats=cache_runner.CacheRunStats(
            requested=0,
            fetched=0,
            cached=0,
            errors=0,
            elapsed_seconds=1.25,
        ),
    )

    main.main(
        [
            "fetch",
            "--cache-root",
            "/mnt/hdd/repositories",
            "--result-path",
            str(tmp_path / "fetch_results.jsonl"),
            "--limit",
            "20",
        ],
    )

    candidates.assert_called_once()
    cache.assert_called_once_with(
        root=main.Path("/mnt/hdd/repositories"),
        command="git",
        timeout_seconds=3600,
    )
    runner.assert_called_once_with(cache=cache.return_value, workers=4)
    assert json.loads(capsys.readouterr().out)["elapsed_seconds"] == 1.25


def test_github_credential_falls_back_to_authenticated_gh_cli(mocker: MockerFixture) -> None:
    mocker.patch.dict(os.environ, {"GITHUB_TOKEN": "", "GH_TOKEN": ""})
    run = mocker.patch(
        "main.subprocess.run",
        autospec=True,
        return_value=CompletedProcess(args=[], returncode=0, stdout="github-credential\n", stderr=""),
    )

    credential = main.github_credential()

    assert credential == "github-credential"
    run.assert_called_once_with(
        ["gh", "auth", "token"],
        capture_output=True,
        text=True,
        check=False,
    )


def test_validate_reports_tracked_repository_candidates(capsys: pytest.CaptureFixture[str]) -> None:
    main.main(["validate"])

    report = json.loads(capsys.readouterr().out)
    assert report["input_dir"] == "docs/repository-candidates"
    assert report["repositories"] == 4935
    assert report["unique_revisions"] == 4935
    assert report["last_commit_minimum"] == "2026-01-01T00:00:00+00:00"
    assert "snapshot_cutoff" not in report
    assert report["languages"] == {
        "Java": 658,
        "JavaScript": 829,
        "Python": 1841,
        "TypeScript": 1607,
    }
    assert report["selection_criteria"] == {
        "contributors_minimum": 10,
        "forks_minimum": 200,
        "is_fork": False,
        "languages": ["Java", "JavaScript", "Python", "TypeScript"],
        "stargazers_minimum": 1000,
        "total_issues_minimum": 200,
        "total_pull_requests_minimum": 200,
    }
    assert report["status"] == "valid"
