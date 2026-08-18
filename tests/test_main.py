import json
import os
from dataclasses import dataclass
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import MagicMock

import pytest
from pytest_mock import MockerFixture

import batch_runner
import cache_runner
import codex_cli_client
import guideline_review
import main
import markdown_audit
import markdown_batch
import markdown_candidate_extraction
import markdown_evaluation
import markdown_filename_audit
import markdown_full_review
import markdown_review
import repository_sampling
import repository_workspace


@dataclass(frozen=True, slots=True)
class _CacheCollectionScenario:
    tmp_path: Path
    candidates: tuple[MagicMock, ...]
    scheduled: MagicMock
    ineligible_scheduled: MagicMock
    later_scheduled: MagicMock
    load_allowlist: MagicMock
    path_fingerprints: MagicMock
    baseline_counts: dict[str, int]
    count_baseline: MagicMock
    schedule: MagicMock
    validate_schedule: MagicMock
    write_schedule: MagicMock
    prior_collection: main.guideline_collection.PriorCollection
    load_prior_collection: MagicMock
    validate_prior_compatibility: MagicMock
    validate_prior_screenings: MagicMock
    validate_prior_manifest: MagicMock
    store_factory: MagicMock
    store: MagicMock
    validate_review: MagicMock
    client: MagicMock
    collect: MagicMock
    write_reports: MagicMock
    output: dict[str, object]


def _write_pending_collection(output_dir: Path, *, target_reached: bool = True) -> None:
    output_dir.mkdir(parents=True)
    (output_dir / "collection_configuration.json").write_text(
        '{"license_policy_status":"pending"}\n',
        encoding="utf-8",
    )
    (output_dir / "collection_summary.json").write_text(
        json.dumps({"target_reached": target_reached}),
        encoding="utf-8",
    )


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


def test_apply_guideline_checklist_writes_validated_review_outputs(
    mocker: MockerFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    apply_checklist = mocker.patch(
        "main.guideline_review.apply_guideline_checklist",
        autospec=True,
        return_value=guideline_review.GuidelineReviewReport(
            input_files=391,
            accepted_files=120,
            duplicate_files=4,
            not_found_files=267,
            reviewed_repositories=86,
            accepted_repositories=80,
            rejected_repositories=6,
            output_dir=Path("experiments/collection/manual-review/applied-round-1"),
        ),
    )

    main.main(
        [
            "apply-guideline-checklist",
            "--checklist-csv",
            "experiments/collection/manual-review/checklist_done.csv",
            "--output-dir",
            "experiments/collection/manual-review/applied-round-1",
        ],
    )

    apply_checklist.assert_called_once_with(
        checklist_path=Path("experiments/collection/manual-review/checklist_done.csv"),
        output_dir=Path("experiments/collection/manual-review/applied-round-1"),
    )
    assert json.loads(capsys.readouterr().out) == {
        "accepted_files": 120,
        "accepted_repositories": 80,
        "duplicate_files": 4,
        "input_files": 391,
        "not_found_files": 267,
        "output_dir": "experiments/collection/manual-review/applied-round-1",
        "rejected_repositories": 6,
        "reviewed_repositories": 86,
        "status": "valid",
        "advances_collection": False,
        "next_action": "resume collection with this completed checklist",
    }


def test_prepare_guideline_license_review_accepts_a_provisional_collection() -> None:
    arguments = main._parser().parse_args(
        [
            "prepare-guideline-license-review",
            "--collection-dir",
            "experiments/collection",
            "--output-dir",
            "experiments/collection/license-review",
        ],
    )

    assert arguments.collection_dir == Path("experiments/collection")
    assert arguments.output_dir == Path("experiments/collection/license-review")


def test_apply_guideline_license_allowlist_accepts_review_and_policy_files() -> None:
    arguments = main._parser().parse_args(
        [
            "apply-guideline-license-allowlist",
            "--repository-licenses-csv",
            "experiments/collection/license-review/repository_licenses.csv",
            "--allowlist-csv",
            "experiments/collection/license-review/license_allowlist.csv",
            "--output-dir",
            "experiments/collection/license-review/applied",
        ],
    )

    assert arguments.repository_licenses_csv == Path(
        "experiments/collection/license-review/repository_licenses.csv",
    )
    assert arguments.allowlist_csv == Path("experiments/collection/license-review/license_allowlist.csv")
    assert arguments.output_dir == Path("experiments/collection/license-review/applied")


def test_prepare_guideline_license_review_prints_repository_counts(
    mocker: MockerFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    report = mocker.Mock(
        repositories=120,
        license_names=10,
        blank_license_repositories=3,
        other_license_repositories=12,
        output_dir=Path("experiments/collection/license-review"),
    )
    prepare = mocker.patch(
        "main.guideline_license.prepare_collection_license_review",
        autospec=True,
        return_value=report,
    )

    main.main(
        [
            "prepare-guideline-license-review",
            "--collection-dir",
            "experiments/collection",
            "--output-dir",
            "experiments/collection/license-review",
        ],
    )

    prepare.assert_called_once_with(
        collection_dir=Path("experiments/collection"),
        output_dir=Path("experiments/collection/license-review"),
    )
    assert json.loads(capsys.readouterr().out) == {
        "blank_license_repositories": 3,
        "license_names": 10,
        "other_license_repositories": 12,
        "output_dir": "experiments/collection/license-review",
        "repositories": 120,
        "status": "prepared",
        "next_action": "create an allowlist and resume collection with --license-allowlist-csv",
    }


def test_apply_guideline_license_allowlist_prints_selection_counts(
    mocker: MockerFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    report = mocker.Mock(
        input_repositories=120,
        accepted_repositories=105,
        rejected_repositories=15,
        allowlisted_license_names=4,
        output_dir=Path("experiments/collection/license-review/applied"),
    )
    apply_allowlist = mocker.patch(
        "main.guideline_license.apply_license_allowlist",
        autospec=True,
        return_value=report,
    )

    main.main(
        [
            "apply-guideline-license-allowlist",
            "--repository-licenses-csv",
            "experiments/collection/license-review/repository_licenses.csv",
            "--allowlist-csv",
            "experiments/collection/license-review/license_allowlist.csv",
            "--output-dir",
            "experiments/collection/license-review/applied",
        ],
    )

    apply_allowlist.assert_called_once_with(
        repository_licenses_path=Path(
            "experiments/collection/license-review/repository_licenses.csv",
        ),
        allowlist_path=Path("experiments/collection/license-review/license_allowlist.csv"),
        output_dir=Path("experiments/collection/license-review/applied"),
    )
    assert json.loads(capsys.readouterr().out) == {
        "accepted_repositories": 105,
        "allowlisted_license_names": 4,
        "input_repositories": 120,
        "output_dir": "experiments/collection/license-review/applied",
        "rejected_repositories": 15,
        "preview_only": True,
        "status": "preview_complete",
        "next_action": "resume collection with --license-allowlist-csv",
    }


def test_finalize_guideline_collection_accepts_all_review_sources() -> None:
    arguments = main._parser().parse_args(
        [
            "finalize-guideline-collection",
            "--collection-dir",
            "experiments/collection",
            "--baseline-checklist",
            "experiments/baseline-1/checklist.csv",
            "--baseline-checklist",
            "experiments/baseline-2/checklist.csv",
            "--human-checklist",
            "experiments/collection/manual-review/checklist.csv",
            "--license-allowlist-csv",
            "experiments/collection/license-review/license_allowlist.csv",
            "--output-dir",
            "experiments/collection/final",
        ],
    )

    assert arguments.collection_dir == Path("experiments/collection")
    assert arguments.baseline_checklist == [
        Path("experiments/baseline-1/checklist.csv"),
        Path("experiments/baseline-2/checklist.csv"),
    ]
    assert arguments.human_checklist == Path("experiments/collection/manual-review/checklist.csv")
    assert arguments.license_allowlist_csv == Path(
        "experiments/collection/license-review/license_allowlist.csv",
    )
    assert arguments.output_dir == Path("experiments/collection/final")


def test_finalize_guideline_collection_prints_validated_counts(
    mocker: MockerFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    finalize = mocker.patch(
        "main.guideline_finalization.finalize_guideline_collection",
        autospec=True,
        return_value=main.guideline_finalization.GuidelineFinalizationReport(
            repositories=120,
            guideline_files=454,
            baseline_repositories=34,
            new_repositories=86,
            baseline_guideline_files=111,
            new_guideline_files=343,
            output_dir=Path("experiments/collection/final"),
        ),
    )

    main.main(
        [
            "finalize-guideline-collection",
            "--collection-dir",
            "experiments/collection",
            "--baseline-checklist",
            "experiments/baseline-1/checklist.csv",
            "--baseline-checklist",
            "experiments/baseline-2/checklist.csv",
            "--human-checklist",
            "experiments/collection/manual-review/checklist.csv",
            "--license-allowlist-csv",
            "experiments/collection/license-review/license_allowlist.csv",
            "--output-dir",
            "experiments/collection/final",
        ],
    )

    finalize.assert_called_once_with(
        collection_dir=Path("experiments/collection"),
        baseline_checklist_paths=(
            Path("experiments/baseline-1/checklist.csv"),
            Path("experiments/baseline-2/checklist.csv"),
        ),
        human_checklist_path=Path("experiments/collection/manual-review/checklist.csv"),
        license_allowlist_path=Path(
            "experiments/collection/license-review/license_allowlist.csv",
        ),
        output_dir=Path("experiments/collection/final"),
    )
    assert json.loads(capsys.readouterr().out) == {
        "baseline_guideline_files": 111,
        "baseline_repositories": 34,
        "guideline_files": 454,
        "new_guideline_files": 343,
        "new_repositories": 86,
        "output_dir": "experiments/collection/final",
        "repositories": 120,
        "status": "complete",
    }


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


def test_classify_markdown_run_cache_uses_fixed_settings_and_local_paths() -> None:
    arguments = main._parser().parse_args(
        [
            "classify-markdown",
            "run-cache",
            "--candidate-csv",
            "output/candidates/markdown_filename_files.csv",
            "--output-dir",
            "output/classification",
            "--cache-root",
            "/hdd/shigyo/swe-conform-repositories",
        ],
    )

    assert arguments.provider == "bedrock"
    assert arguments.bedrock_region == "us-east-1"
    assert arguments.model == "gpt-5.6-luna"
    assert arguments.reasoning_effort == "max"
    assert arguments.max_output_tokens == 32_000
    assert arguments.workers == 16
    assert arguments.blob_batch_size == 64
    assert arguments.max_input_bytes == 200_000
    assert arguments.max_model_attempts == 3
    assert arguments.max_retrieval_attempts == 2


def test_collect_guideline_repositories_uses_the_fixed_collection_defaults() -> None:
    arguments = main._parser().parse_args(
        [
            "collect-guideline-repositories",
            "--input-dir",
            "docs/repository-candidates",
            "--output-dir",
            "output/guideline-collection",
            "--cache-root",
            "/hdd/shigyo/swe-conform-repositories",
            "--baseline-checklist",
            "experiments/50/checklist_full.csv",
            "--baseline-checklist",
            "experiments/20/checklist2_full.csv",
            "--exclude-csv",
            "experiments/50/input/candidates.csv",
            "--exclude-csv",
            "experiments/20/input/candidates.csv",
            "--license-allowlist-csv",
            "experiments/collection/license-review/license_allowlist.csv",
        ],
    )

    assert arguments.target_total_repositories == 120
    assert arguments.sample_seed == 20260807
    assert arguments.repository_workers == 4
    assert arguments.file_workers == 4
    assert arguments.max_repository_attempts == 3
    assert arguments.max_model_attempts == 3
    assert arguments.max_retrieval_attempts == 2
    assert len(arguments.baseline_checklist) == 2
    assert len(arguments.exclude_csv) == 2


def test_collect_guideline_repositories_accepts_a_human_license_allowlist() -> None:
    arguments = main._parser().parse_args(
        [
            "collect-guideline-repositories",
            "--repository-source",
            "github",
            "--output-dir",
            "output/guideline-collection",
            "--baseline-checklist",
            "experiments/50/checklist_full.csv",
            "--exclude-csv",
            "experiments/50/input/candidates.csv",
            "--license-allowlist-csv",
            "experiments/collection/license-review/license_allowlist.csv",
        ],
    )

    assert arguments.license_allowlist_csv == Path(
        "experiments/collection/license-review/license_allowlist.csv",
    )


def test_collect_guideline_repositories_can_start_before_license_review() -> None:
    arguments = main._parser().parse_args(
        [
            "collect-guideline-repositories",
            "--repository-source",
            "github",
            "--output-dir",
            "output/guideline-collection",
            "--baseline-checklist",
            "experiments/50/checklist_full.csv",
            "--exclude-csv",
            "experiments/50/input/candidates.csv",
        ],
    )

    assert arguments.license_allowlist_csv is None


def test_collection_starts_with_provisional_repositories_before_license_review(
    mocker: MockerFixture,
) -> None:
    candidates = (
        mocker.Mock(repository="baseline/project", license_name="GNU GPL v3.0"),
        mocker.Mock(repository="new/project", license_name="MIT License"),
    )
    mocker.patch(
        "main.guideline_collection.load_baseline_repositories",
        autospec=True,
        return_value={"baseline/project"},
    )
    mocker.patch(
        "main.guideline_collection.load_manual_review_state",
        autospec=True,
        return_value=main.guideline_collection.ManualReviewState(set(), set()),
    )

    policy = main.guideline_license.load_collection_license_policy(candidates, allowlist_path=None)
    review = main._collection_review_state(
        candidates=candidates,
        baseline_checklists=(Path("baseline.csv"),),
        human_checklist=None,
        eligible_repositories=set(policy.eligible_repositories),
    )

    assert not policy.is_reviewed
    assert review.baseline_repositories == frozenset({"baseline/project"})
    assert review.eligible_repositories == frozenset({"baseline/project", "new/project"})
    assert not review.manual_review.confirmed_repositories


def test_collection_rejects_file_review_before_license_review() -> None:
    policy = main.guideline_license.CollectionLicensePolicy(
        is_reviewed=False,
        allowlist=None,
        eligible_repositories=frozenset(),
    )

    with pytest.raises(ValueError, match="license review must finish before file review"):
        main._validate_collection_review_arguments(
            policy,
            human_checklist=Path("checklist.csv"),
            review_output_checklist=None,
            prior_collection_dir=None,
            output_dir=Path("collection"),
        )


def test_provisional_collection_records_pending_license_policy() -> None:
    policy = main.guideline_license.CollectionLicensePolicy(
        is_reviewed=False,
        allowlist=None,
        eligible_repositories=frozenset({"new/project"}),
    )

    configuration = main._collection_license_configuration(
        policy,
        allowlist_path=None,
        ineligible_reviewed_repositories=frozenset(),
    )

    assert configuration == {
        "license_policy_status": "pending",
        "license_allowlist": [],
        "license_allowlist_fingerprints": {},
        "license_ineligible_reviewed_repositories": [],
    }


def test_provisional_collection_rejects_a_file_review_output() -> None:
    policy = main.guideline_license.CollectionLicensePolicy(
        is_reviewed=False,
        allowlist=None,
        eligible_repositories=frozenset(),
    )

    with pytest.raises(ValueError, match="license review must finish before file review"):
        main._validate_collection_review_arguments(
            policy,
            human_checklist=None,
            review_output_checklist=Path("checklist.csv"),
            prior_collection_dir=None,
            output_dir=Path("collection"),
        )


def test_provisional_collection_rejects_existing_file_review_artifacts(tmp_path: Path) -> None:
    output_dir = tmp_path / "collection"
    _write_pending_collection(output_dir)
    (output_dir / "manual-review").mkdir()
    policy = main.guideline_license.CollectionLicensePolicy(
        is_reviewed=False,
        allowlist=None,
        eligible_repositories=frozenset(),
    )

    with pytest.raises(ValueError, match="provisional collection contains file-review artifacts"):
        main._validate_collection_review_arguments(
            policy,
            human_checklist=None,
            review_output_checklist=None,
            prior_collection_dir=None,
            output_dir=output_dir,
        )


def test_provisional_collection_accepts_a_completed_review_from_a_prior_collection() -> None:
    policy = main.guideline_license.CollectionLicensePolicy(
        is_reviewed=False,
        allowlist=None,
        eligible_repositories=frozenset(),
    )

    main._validate_collection_review_arguments(
        policy,
        human_checklist=Path("checklist_done.csv"),
        review_output_checklist=None,
        prior_collection_dir=Path("prior-collection"),
        output_dir=Path("new-collection"),
    )


def test_license_policy_transition_rejects_a_new_file_review(tmp_path: Path) -> None:
    output_dir = tmp_path / "collection"
    _write_pending_collection(output_dir)
    policy = main.guideline_license.CollectionLicensePolicy(
        is_reviewed=True,
        allowlist=main.guideline_license.LicenseAllowlist(frozenset({"MIT License"})),
        eligible_repositories=frozenset(),
    )

    with pytest.raises(ValueError, match="file review cannot be applied during license policy transition"):
        main._validate_collection_review_arguments(
            policy,
            human_checklist=Path("checklist_done.csv"),
            review_output_checklist=None,
            prior_collection_dir=None,
            output_dir=output_dir,
        )


def test_license_policy_cannot_be_applied_before_provisional_target_is_reached(tmp_path: Path) -> None:
    (tmp_path / "collection_configuration.json").write_text(
        '{"license_policy_status":"pending"}\n',
        encoding="utf-8",
    )
    (tmp_path / "collection_summary.json").write_text(
        '{"target_reached":false}\n',
        encoding="utf-8",
    )
    policy = main.guideline_license.CollectionLicensePolicy(
        is_reviewed=True,
        allowlist=main.guideline_license.LicenseAllowlist(frozenset({"MIT License"})),
        eligible_repositories=frozenset(),
    )

    with pytest.raises(ValueError, match="provisional repository target is not reached"):
        main._validate_license_policy_transition(tmp_path, policy)


def test_new_collection_must_start_before_license_policy_is_applied(tmp_path: Path) -> None:
    policy = main.guideline_license.CollectionLicensePolicy(
        is_reviewed=True,
        allowlist=main.guideline_license.LicenseAllowlist(frozenset({"MIT License"})),
        eligible_repositories=frozenset(),
    )

    with pytest.raises(ValueError, match="start the provisional collection without an allowlist"):
        main._validate_license_policy_transition(tmp_path, policy)


def test_applied_license_policy_cannot_return_to_pending(tmp_path: Path) -> None:
    (tmp_path / "collection_configuration.json").write_text(
        '{"license_policy_status":"applied"}\n',
        encoding="utf-8",
    )
    policy = main.guideline_license.CollectionLicensePolicy(
        is_reviewed=False,
        allowlist=None,
        eligible_repositories=frozenset(),
    )

    with pytest.raises(ValueError, match="applied license policy requires its allowlist"):
        main._validate_license_policy_transition(tmp_path, policy)


def test_collect_guideline_repositories_accepts_github_without_a_cache_root() -> None:
    arguments = main._parser().parse_args(
        [
            "collect-guideline-repositories",
            "--repository-source",
            "github",
            "--output-dir",
            "output/guideline-collection",
            "--baseline-checklist",
            "experiments/50/checklist_full.csv",
            "--exclude-csv",
            "experiments/50/input/candidates.csv",
            "--license-allowlist-csv",
            "experiments/collection/license-review/license_allowlist.csv",
        ],
    )

    assert arguments.repository_source == "github"
    assert arguments.cache_root is None


def test_collect_guideline_repositories_accepts_a_screening_cost_limit() -> None:
    arguments = main._parser().parse_args(
        [
            "collect-guideline-repositories",
            "--repository-source",
            "github",
            "--output-dir",
            "output/guideline-collection",
            "--baseline-checklist",
            "experiments/50/checklist_full.csv",
            "--exclude-csv",
            "experiments/50/input/candidates.csv",
            "--license-allowlist-csv",
            "experiments/collection/license-review/license_allowlist.csv",
            "--max-screened-repositories",
            "200",
        ],
    )

    assert arguments.max_screened_repositories == 200


def test_collect_guideline_repositories_accepts_a_separate_next_round_checklist() -> None:
    arguments = main._parser().parse_args(
        [
            "collect-guideline-repositories",
            "--repository-source",
            "github",
            "--output-dir",
            "output/guideline-collection",
            "--baseline-checklist",
            "experiments/50/checklist_full.csv",
            "--exclude-csv",
            "experiments/50/input/candidates.csv",
            "--license-allowlist-csv",
            "experiments/collection/license-review/license_allowlist.csv",
            "--human-checklist",
            "output/guideline-collection/manual-review/checklist_done.csv",
            "--review-output-checklist",
            "output/guideline-collection/manual-review/checklist_round_2.csv",
        ],
    )

    assert arguments.review_output_checklist == Path(
        "output/guideline-collection/manual-review/checklist_round_2.csv",
    )


def test_collect_guideline_repositories_accepts_a_prior_collection() -> None:
    arguments = main._parser().parse_args(
        [
            "collect-guideline-repositories",
            "--repository-source",
            "github",
            "--output-dir",
            "output/guideline-collection-balanced",
            "--baseline-checklist",
            "experiments/50/checklist_full.csv",
            "--exclude-csv",
            "experiments/50/input/candidates.csv",
            "--license-allowlist-csv",
            "experiments/collection/license-review/license_allowlist.csv",
            "--prior-collection-dir",
            "experiments/collection",
        ],
    )

    assert arguments.prior_collection_dir == Path("experiments/collection")


def test_collect_guideline_repositories_runs_the_cache_only_file_pipeline(
    capsys: pytest.CaptureFixture[str],
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    arguments = main._parser().parse_args(
        [
            "collect-guideline-repositories",
            "--input-dir",
            str(tmp_path / "population"),
            "--output-dir",
            str(tmp_path / "output"),
            "--cache-root",
            str(tmp_path / "cache"),
            "--baseline-checklist",
            str(tmp_path / "baseline.csv"),
            "--exclude-csv",
            str(tmp_path / "excluded.csv"),
            "--license-allowlist-csv",
            str(tmp_path / "license_allowlist.csv"),
            "--human-checklist",
            str(tmp_path / "checklist_done.csv"),
            "--review-output-checklist",
            str(tmp_path / "checklist_round_2.csv"),
            "--prior-collection-dir",
            str(tmp_path / "prior-collection"),
        ],
    )
    _write_pending_collection(arguments.output_dir)
    baseline_candidate = mocker.Mock(
        repository="baseline/project",
        revision="b" * 40,
        license_name="MIT License",
    )
    baseline_candidate.fields = {"mainLanguage": "Java"}
    ineligible_baseline_candidate = mocker.Mock(
        repository="baseline/ineligible",
        revision="d" * 40,
        license_name="GNU GPL v3.0",
    )
    ineligible_baseline_candidate.fields = {"mainLanguage": "Java"}
    candidate = mocker.Mock(repository="new/project", revision="a" * 40, license_name="MIT License")
    candidate.fields = {"mainLanguage": "Java"}
    ineligible_candidate = mocker.Mock(
        repository="new/ineligible",
        revision="c" * 40,
        license_name="GNU GPL v3.0",
    )
    ineligible_candidate.fields = {"mainLanguage": "Java"}
    later_candidate = mocker.Mock(
        repository="new/later-project",
        revision="e" * 40,
        license_name="MIT License",
    )
    later_candidate.fields = {"mainLanguage": "Java"}
    scheduled = mocker.Mock(candidate=candidate, sample_order=1, round_number=1, language="Java")
    scheduled.language_population = 10
    ineligible_scheduled = mocker.Mock(
        candidate=ineligible_candidate,
        sample_order=2,
        round_number=2,
        language="Java",
    )
    ineligible_scheduled.language_population = 10
    later_scheduled = mocker.Mock(
        candidate=later_candidate,
        sample_order=3,
        round_number=3,
        language="Java",
    )
    later_scheduled.language_population = 10
    candidates = (
        baseline_candidate,
        ineligible_baseline_candidate,
        candidate,
        ineligible_candidate,
        later_candidate,
    )
    mocker.patch("main.repository.load_repository_candidates", autospec=True, return_value=candidates)
    allowlist = main.guideline_license.LicenseAllowlist(frozenset({"MIT License"}))
    load_allowlist = mocker.patch(
        "main.guideline_license.load_license_allowlist",
        autospec=True,
        return_value=allowlist,
    )
    mocker.patch("main._input_fingerprints", autospec=True, return_value={"population.csv": "hash"})
    path_fingerprints = mocker.patch("main._path_fingerprints", autospec=True, return_value={})
    mocker.patch(
        "main.guideline_collection.load_baseline_repositories",
        autospec=True,
        return_value={"baseline/project", "baseline/ineligible"},
    )
    baseline_counts = {
        "Java": 1,
        "JavaScript": 0,
        "Python": 0,
        "TypeScript": 0,
    }
    count_baseline = mocker.patch(
        "main.guideline_collection.baseline_repository_counts_by_language",
        autospec=True,
        return_value=baseline_counts,
    )
    mocker.patch(
        "main.repository_sampling.load_excluded_repositories",
        autospec=True,
        return_value={"baseline/project"},
    )
    mocker.patch("main.guideline_collection.validate_baseline_exclusions", autospec=True)
    mocker.patch(
        "main.guideline_collection.load_manual_review_state", autospec=True
    ).return_value = main.guideline_collection.ManualReviewState(
        {"new/project", "new/ineligible"},
        set(),
    )
    schedule = mocker.patch(
        "main.repository_sampling.stratified_schedule",
        autospec=True,
        return_value=(scheduled, ineligible_scheduled, later_scheduled),
    )
    validate_schedule = mocker.patch(
        "main.guideline_collection.validate_schedule_covers_language_deficits",
        autospec=True,
    )
    write_schedule = mocker.patch("main.repository_sampling.write_stratified_schedule", autospec=True)
    prior_collection = main.guideline_collection.PriorCollection(
        tmp_path / "prior-collection",
        {},
        (
            main.guideline_collection.RepositoryScreening(scheduled, status="pass"),
            main.guideline_collection.RepositoryScreening(ineligible_scheduled, status="pass"),
        ),
    )
    load_prior_collection = mocker.patch(
        "main.guideline_collection.load_prior_collection",
        autospec=True,
        return_value=prior_collection,
    )
    validate_prior_compatibility = mocker.patch(
        "main.guideline_collection.validate_prior_collection_compatibility",
        autospec=True,
    )
    validate_prior_screenings = mocker.patch(
        "main.guideline_collection.validate_prior_screenings",
        autospec=True,
    )
    validate_prior_manifest = mocker.patch(
        "main.guideline_collection.validate_prior_schedule_manifest",
        autospec=True,
    )
    store_factory = mocker.patch("main.guideline_collection.RepositoryCollectionStore", autospec=True)
    store = store_factory.return_value
    validate_review = mocker.patch("main.guideline_collection.validate_manual_review_state", autospec=True)
    mocker.patch("main.repository_cache.GitRepositoryCache", autospec=True)
    mocker.patch("main.repository_tree.LocalRepositoryTreeClient", autospec=True)
    mocker.patch("main.markdown_filename_audit.MarkdownFilenameAuditor", autospec=True)
    client = mocker.patch("main._collection_classification_client", autospec=True).return_value
    mocker.patch("main.guideline_collection.RepositoryFileProcessor", autospec=True)
    collect = mocker.patch(
        "main.guideline_collection.collect_repositories",
        autospec=True,
        return_value=main.guideline_collection.RepositoryCollectionReport(
            baseline_repositories=1,
            new_repository_target=119,
            confirmed_new_repositories=0,
            pending_new_repositories=0,
            selected_new_repositories=0,
            processed_repositories=1,
            target_reached=False,
            human_target_reached=False,
            carried_screened_repositories=1,
            target_repositories_by_language=dict.fromkeys(baseline_counts, 30),
            baseline_repositories_by_language=baseline_counts,
            confirmed_new_repositories_by_language=dict.fromkeys(baseline_counts, 0),
            pending_new_repositories_by_language=dict.fromkeys(baseline_counts, 0),
            selected_repositories_by_language=baseline_counts,
            remaining_repositories_by_language={
                "Java": 29,
                "JavaScript": 30,
                "Python": 30,
                "TypeScript": 30,
            },
        ),
    )
    write_reports = mocker.patch("main.guideline_collection_reports.write_collection_reports", autospec=True)

    main._collect_guideline_repositories(arguments)

    _assert_cache_collection_scenario(
        _CacheCollectionScenario(
            tmp_path=tmp_path,
            candidates=candidates,
            scheduled=scheduled,
            ineligible_scheduled=ineligible_scheduled,
            later_scheduled=later_scheduled,
            load_allowlist=load_allowlist,
            path_fingerprints=path_fingerprints,
            baseline_counts=baseline_counts,
            count_baseline=count_baseline,
            schedule=schedule,
            validate_schedule=validate_schedule,
            write_schedule=write_schedule,
            prior_collection=prior_collection,
            load_prior_collection=load_prior_collection,
            validate_prior_compatibility=validate_prior_compatibility,
            validate_prior_screenings=validate_prior_screenings,
            validate_prior_manifest=validate_prior_manifest,
            store_factory=store_factory,
            store=store,
            validate_review=validate_review,
            client=client,
            collect=collect,
            write_reports=write_reports,
            output=json.loads(capsys.readouterr().out),
        ),
    )


def _assert_cache_collection_scenario(scenario: _CacheCollectionScenario) -> None:
    scenario.store.initialize.assert_called_once_with()
    configuration = scenario.store_factory.call_args.kwargs["configuration"]
    assert "max_screened_repositories" not in configuration
    assert configuration["schema_version"] == 5
    assert configuration["sampling_method"] == "stratified_random_per_language_until_quota"
    assert configuration["license_allowlist"] == ["MIT License"]
    assert configuration["license_ineligible_reviewed_repositories"] == [
        "baseline/ineligible",
        "new/ineligible",
    ]
    scenario.path_fingerprints.assert_any_call((scenario.tmp_path / "license_allowlist.csv",))
    scenario.path_fingerprints.assert_any_call(
        (
            scenario.tmp_path / "prior-collection" / "collection_configuration.json",
            scenario.tmp_path / "prior-collection" / "repository_attempts.jsonl",
            scenario.tmp_path / "prior-collection" / "sampling_manifest.csv",
        ),
    )
    assert configuration["target_repositories_by_language"] == dict.fromkeys(scenario.baseline_counts, 30)
    assert configuration["baseline_repositories_by_language"] == scenario.baseline_counts
    eligible_manual_review = main.guideline_collection.ManualReviewState({"new/project"}, set())
    eligible_prior_screenings = (scenario.prior_collection.screenings[0],)
    scenario.load_prior_collection.assert_called_once_with(scenario.tmp_path / "prior-collection")
    scenario.validate_prior_compatibility.assert_called_once_with(
        scenario.prior_collection,
        current_configuration=configuration,
    )
    scenario.validate_prior_screenings.assert_called_once_with(
        scenario.prior_collection.screenings,
        schedule=(scenario.scheduled, scenario.ineligible_scheduled, scenario.later_scheduled),
    )
    scenario.validate_prior_manifest.assert_called_once_with(
        scenario.tmp_path / "prior-collection" / "sampling_manifest.csv",
        schedule=(scenario.scheduled, scenario.ineligible_scheduled, scenario.later_scheduled),
    )
    scenario.validate_review.assert_called_once_with(
        eligible_manual_review,
        store=scenario.store,
        prior_screenings=eligible_prior_screenings,
    )
    scenario.load_allowlist.assert_called_once_with(scenario.tmp_path / "license_allowlist.csv")
    scenario.count_baseline.assert_called_once_with(
        {"baseline/project"},
        population=scenario.candidates,
    )
    scenario.schedule.assert_called_once_with(
        scenario.candidates,
        sample_seed=20260807,
        excluded_repositories={"baseline/project"},
    )
    scenario.validate_schedule.assert_called_once_with(
        (scenario.scheduled, scenario.later_scheduled),
        baseline_repository_counts=scenario.baseline_counts,
        target_total_repositories=120,
    )
    scenario.write_schedule.assert_called_once_with(
        scenario.tmp_path / "output" / "sampling_manifest.csv",
        (scenario.scheduled, scenario.ineligible_scheduled, scenario.later_scheduled),
    )
    assert scenario.collect.call_args.args[0] == (scenario.scheduled, scenario.later_scheduled)
    assert scenario.collect.call_args.kwargs["confirmed_repositories"] == {"new/project"}
    assert scenario.collect.call_args.kwargs["prior_screenings"] == eligible_prior_screenings
    assert scenario.collect.call_args.kwargs["baseline_repository_counts"] == scenario.baseline_counts
    assert scenario.write_reports.call_args.kwargs["baseline_repository_counts"] == scenario.baseline_counts
    assert scenario.write_reports.call_args.kwargs["schedule"] == (scenario.scheduled, scenario.later_scheduled)
    assert scenario.write_reports.call_args.kwargs["prior_screenings"] == eligible_prior_screenings
    assert scenario.write_reports.call_args.kwargs["license_ineligible_reviewed_repositories"] == 2
    assert scenario.write_reports.call_args.kwargs["export_manual_review"] is False
    assert scenario.write_reports.call_args.kwargs["review_checklist_path"] == scenario.tmp_path / "checklist_done.csv"
    assert (
        scenario.write_reports.call_args.kwargs["review_output_checklist_path"]
        == scenario.tmp_path / "checklist_round_2.csv"
    )
    scenario.client.close.assert_called_once_with()
    assert scenario.output["license_ineligible_reviewed_repositories"] == 2
    assert scenario.output["carried_screened_repositories"] == 1
    assert scenario.output["license_policy_status"] == "applied"
    assert scenario.output["workflow_stage"] == "needs_replenishment"
    assert scenario.output["next_action"] == "resume eligible repository screening"
    assert scenario.output["manual_review_ready"] is False
    assert scenario.output["target_repositories_by_language"] == dict.fromkeys(scenario.baseline_counts, 30)
    assert scenario.output["selected_repositories_by_language"] == scenario.baseline_counts
    assert scenario.output["remaining_repositories_by_language"] == {
        "Java": 29,
        "JavaScript": 30,
        "Python": 30,
        "TypeScript": 30,
    }


def test_collect_guideline_repositories_runs_the_github_file_pipeline(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "output"
    arguments = main._parser().parse_args(
        [
            "collect-guideline-repositories",
            "--repository-source",
            "github",
            "--input-dir",
            str(tmp_path / "population"),
            "--output-dir",
            str(output_dir),
            "--baseline-checklist",
            str(tmp_path / "baseline.csv"),
            "--exclude-csv",
            str(tmp_path / "excluded.csv"),
            "--license-allowlist-csv",
            str(tmp_path / "license_allowlist.csv"),
            "--max-screened-repositories",
            "200",
        ],
    )
    _write_pending_collection(arguments.output_dir)
    baseline_candidate = mocker.Mock(
        repository="baseline/project",
        revision="b" * 40,
        license_name="MIT License",
    )
    baseline_candidate.fields = {"mainLanguage": "Java"}
    candidate = mocker.Mock(repository="new/project", revision="a" * 40, license_name="MIT License")
    candidate.fields = {"mainLanguage": "Java"}
    scheduled = mocker.Mock(candidate=candidate, sample_order=1, round_number=1, language="Java")
    scheduled.language_population = 10
    mocker.patch(
        "main.repository.load_repository_candidates",
        autospec=True,
        return_value=(baseline_candidate, candidate),
    )
    mocker.patch(
        "main.guideline_license.load_license_allowlist",
        autospec=True,
        return_value=main.guideline_license.LicenseAllowlist(frozenset({"MIT License"})),
    )
    mocker.patch("main._input_fingerprints", autospec=True, return_value={"population.csv": "hash"})
    mocker.patch("main._path_fingerprints", autospec=True, return_value={})
    mocker.patch(
        "main.guideline_collection.load_baseline_repositories",
        autospec=True,
        return_value={"baseline/project"},
    )
    mocker.patch(
        "main.guideline_collection.baseline_repository_counts_by_language",
        autospec=True,
        return_value={
            "Java": 1,
            "JavaScript": 0,
            "Python": 0,
            "TypeScript": 0,
        },
    )
    mocker.patch(
        "main.repository_sampling.load_excluded_repositories",
        autospec=True,
        return_value={"baseline/project"},
    )
    mocker.patch("main.guideline_collection.validate_baseline_exclusions", autospec=True)
    mocker.patch(
        "main.guideline_collection.load_manual_review_state",
        autospec=True,
        return_value=main.guideline_collection.ManualReviewState(set(), set()),
    )
    mocker.patch("main.repository_sampling.stratified_schedule", autospec=True, return_value=(scheduled,))
    mocker.patch("main.guideline_collection.validate_schedule_covers_language_deficits", autospec=True)
    mocker.patch("main.repository_sampling.write_stratified_schedule", autospec=True)
    store = mocker.patch("main.guideline_collection.RepositoryCollectionStore", autospec=True).return_value
    mocker.patch("main.guideline_collection.validate_manual_review_state", autospec=True)
    credential = mocker.patch("main.github_credential", autospec=True, return_value="github-credential")
    github = mocker.patch("main.github_client.GitHubClient", autospec=True)
    persistent = mocker.patch("main.github_repository.PersistentGitHubRepositoryClient", autospec=True)
    persistent.return_value.report_metrics.return_value = {
        "github_requests": 4,
        "github_rate_limit_wait_seconds": 0.0,
        "source_content_downloads": 3,
        "source_content_cache_hits": 2,
    }
    mocker.patch("main.markdown_filename_audit.MarkdownFilenameAuditor", autospec=True)
    responses = mocker.patch("main._collection_classification_client", autospec=True).return_value
    processor = mocker.patch("main.guideline_collection.RepositoryFileProcessor", autospec=True).return_value
    collect = mocker.patch(
        "main.guideline_collection.collect_repositories",
        autospec=True,
        return_value=main.guideline_collection.RepositoryCollectionReport(
            baseline_repositories=1,
            new_repository_target=119,
            confirmed_new_repositories=0,
            pending_new_repositories=0,
            selected_new_repositories=0,
            processed_repositories=1,
            target_reached=True,
            human_target_reached=False,
        ),
    )
    write_reports = mocker.patch("main.guideline_collection_reports.write_collection_reports", autospec=True)

    main._collect_guideline_repositories(arguments)

    credential.assert_called_once_with()
    github.assert_called_once_with(token="github-credential")
    persistent.assert_called_once_with(
        client=github.return_value,
        content_root=output_dir / "source-content",
    )
    collect.assert_called_once()
    assert collect.call_args.kwargs["processor"] is processor
    assert collect.call_args.kwargs["max_screened_repositories"] == 200
    write_reports.assert_called_once()
    assert write_reports.call_args.kwargs["source_metrics"] is persistent.return_value.report_metrics
    assert write_reports.call_args.kwargs["export_manual_review"] is True
    responses.close.assert_called_once_with()
    github.return_value.close.assert_called_once_with()
    store.initialize.assert_called_once_with()


def test_classify_markdown_run_cache_never_creates_a_github_client(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    candidate_csv = tmp_path / "candidates" / "markdown_filename_files.csv"
    candidate_csv.parent.mkdir()
    candidate_csv.write_text("name\n", encoding="utf-8")
    repository_summary_csv = candidate_csv.parent / "repository_filename_summary.csv"
    repository_summary_csv.write_text("name\n", encoding="utf-8")
    credential = mocker.patch("main.github_credential", autospec=True)
    github = mocker.patch("main.github_client.GitHubClient", autospec=True)
    cache = mocker.patch("main.repository_cache.GitRepositoryCache", autospec=True)
    local = mocker.patch("main.repository_tree.LocalRepositoryTreeClient", autospec=True)
    responses = mocker.patch("main._classification_client", autospec=True)
    run = mocker.patch(
        "main.markdown_cache_classification.run_cache_classification",
        autospec=True,
        return_value={"requested": 10, "completed": 10, "errors": 0},
    )
    output_dir = tmp_path / "classification"

    main.main(
        [
            "classify-markdown",
            "run-cache",
            "--candidate-csv",
            str(candidate_csv),
            "--output-dir",
            str(output_dir),
            "--cache-root",
            "/hdd/shigyo/swe-conform-repositories",
        ],
    )

    credential.assert_not_called()
    github.assert_not_called()
    cache.assert_called_once_with(root=Path("/hdd/shigyo/swe-conform-repositories"), command="git")
    local.assert_called_once_with(cache=cache.return_value, command="git")
    run.assert_called_once_with(
        candidate_csv=candidate_csv,
        repository_summary_csv=repository_summary_csv,
        output_dir=output_dir,
        repository_client=local.return_value,
        snapshot_inspector=cache.return_value,
        skip_incomplete_repositories=False,
        excluded_repositories=(),
        responses_client=responses.return_value,
        provider="bedrock",
        region="us-east-1",
        model="gpt-5.6-luna",
        reasoning_effort="max",
        max_output_tokens=32_000,
        workers=16,
        blob_batch_size=64,
        max_input_bytes=200_000,
        max_model_attempts=3,
        max_retrieval_attempts=2,
    )
    responses.return_value.close.assert_called_once_with()


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


def test_markdown_filename_audit_cache_only_never_creates_a_github_client(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    mocker.patch("main.repository.load_repository_candidates", autospec=True, return_value=())
    mocker.patch("main.markdown_audit.load_agent_evidence", autospec=True, return_value={})
    credential = mocker.patch("main.github_credential", autospec=True)
    github = mocker.patch("main.github_client.GitHubClient", autospec=True)
    cache = mocker.patch("main.repository_cache.GitRepositoryCache", autospec=True)
    local = mocker.patch("main.repository_tree.LocalRepositoryTreeClient", autospec=True)
    auditor = mocker.patch("main.markdown_filename_audit.MarkdownFilenameAuditor", autospec=True)
    runner = mocker.patch("main.markdown_filename_audit.MarkdownFilenameAuditRunner", autospec=True)
    runner.return_value.run.return_value = markdown_filename_audit.MarkdownFilenameAuditReport(
        results=(),
        stats=markdown_filename_audit.MarkdownFilenameAuditStats(
            requested=0,
            completed=0,
            errors=0,
            elapsed_seconds=0.0,
        ),
    )
    mocker.patch("main.markdown_filename_audit.write_reports", autospec=True)

    main.main(
        [
            "audit-markdown-filenames",
            "--input-dir",
            "experiments/input",
            "--output-dir",
            str(tmp_path),
            "--cache-root",
            "/mnt/hdd/repositories",
            "--cache-only",
        ],
    )

    credential.assert_not_called()
    github.assert_not_called()
    cache.assert_called_once_with(root=Path("/mnt/hdd/repositories"), command="git")
    local.assert_called_once_with(cache=cache.return_value, command="git")
    auditor.assert_called_once_with(client=local.return_value, agent_evidence={})


def test_markdown_filename_cache_only_accepts_snapshot_safety_options() -> None:
    arguments = main._parser().parse_args(
        [
            "audit-markdown-filenames",
            "--input-dir",
            "experiments/input",
            "--output-dir",
            "experiments/output",
            "--cache-root",
            "/mnt/hdd/repositories",
            "--cache-only",
            "--skip-incomplete-repositories",
            "--exclude-repository",
            "revanced/revanced-patches",
        ],
    )

    assert arguments.skip_incomplete_repositories is True
    assert arguments.exclude_repository == ["revanced/revanced-patches"]


def test_markdown_filename_snapshot_safety_requires_cache_only(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="--cache-only"):
        main.main(
            [
                "audit-markdown-filenames",
                "--input-dir",
                str(tmp_path),
                "--output-dir",
                str(tmp_path / "output"),
                "--skip-incomplete-repositories",
            ],
        )


def test_cache_classification_accepts_snapshot_safety_options() -> None:
    arguments = main._parser().parse_args(
        [
            "classify-markdown",
            "run-cache",
            "--candidate-csv",
            "experiments/candidates.csv",
            "--output-dir",
            "experiments/output",
            "--cache-root",
            "/mnt/hdd/repositories",
            "--skip-incomplete-repositories",
            "--exclude-repository",
            "revanced/revanced-patches",
        ],
    )

    assert arguments.skip_incomplete_repositories is True
    assert arguments.exclude_repository == ["revanced/revanced-patches"]


def test_markdown_filename_audit_cache_only_uses_the_resumable_candidate_store(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    candidates = (mocker.sentinel.candidate,)
    mocker.patch("main.repository.load_repository_candidates", autospec=True, return_value=candidates)
    mocker.patch("main.markdown_audit.load_agent_evidence", autospec=True, return_value={})
    cache = mocker.patch("main.repository_cache.GitRepositoryCache", autospec=True)
    mocker.patch("main.repository_tree.LocalRepositoryTreeClient", autospec=True)
    auditor = mocker.patch("main.markdown_filename_audit.MarkdownFilenameAuditor", autospec=True)
    store = mocker.patch("main.markdown_candidate_store.MarkdownCandidateStore", autospec=True)
    store.return_value.report.return_value = markdown_filename_audit.MarkdownFilenameAuditReport(
        results=(),
        stats=markdown_filename_audit.MarkdownFilenameAuditStats(
            requested=1,
            completed=1,
            errors=0,
            elapsed_seconds=0.0,
        ),
    )
    run = mocker.patch(
        "main.markdown_candidate_extraction.run_candidate_extraction",
        autospec=True,
        return_value=markdown_candidate_extraction.CandidateExtractionStats(
            requested=1,
            skipped=0,
            evaluated=1,
            elapsed_seconds=1.25,
        ),
    )

    main.main(
        [
            "audit-markdown-filenames",
            "--input-dir",
            "experiments/input",
            "--output-dir",
            str(tmp_path),
            "--cache-root",
            "/mnt/hdd/repositories",
            "--cache-only",
            "--skip-incomplete-repositories",
            "--exclude-repository",
            "revanced/revanced-patches",
        ],
    )

    store.assert_called_once_with(tmp_path, configuration=mocker.ANY)
    store.return_value.initialize.assert_called_once_with()
    run.assert_called_once_with(
        candidates,
        auditor=auditor.return_value,
        store=store.return_value,
        workers=4,
        limit=None,
        on_progress=main._log_candidate_progress,
        snapshot_inspector=cache.return_value,
        skip_incomplete_repositories=True,
        excluded_repositories=("revanced/revanced-patches",),
    )


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
