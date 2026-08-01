"""Tests for prediction-blind calibration evidence export."""

import json
from pathlib import Path

from pytest_mock import MockerFixture

import evidence_export
import guideline_evidence
import repository


def test_write_blind_evidence_uses_the_classifier_payload(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    candidate = repository.RepositoryCandidate(
        repository="example/project",
        revision="0123456789abcdef",
        license_name="MIT License",
        source_file="candidates.csv",
        input_index=0,
        fields={"name": "example/project"},
    )
    collector = mocker.Mock(spec=guideline_evidence.GuidelineEvidenceCollector)
    collector.collect.return_value = guideline_evidence.RepositoryEvidence(
        documents=(
            guideline_evidence.GuidelineDocument(
                path="CONTRIBUTING.md",
                content="Functions must use snake_case names.",
            ),
        ),
        tree_truncated=False,
    )
    output_path = tmp_path / "blind_evidence.json"

    evidence_export.write_blind_evidence((candidate,), collector, output_path)

    assert json.loads(output_path.read_text(encoding="utf-8")) == [
        {
            "documents": [
                {
                    "content": "Functions must use snake_case names.",
                    "path": "CONTRIBUTING.md",
                },
            ],
            "repository": "example/project",
            "revision": "0123456789abcdef",
            "tree_truncated": False,
        },
    ]
    collector.collect.assert_called_once_with("example/project", "0123456789abcdef")
