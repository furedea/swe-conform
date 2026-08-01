import json

import pytest

from main import main


def test_main() -> None:
    main([])


def test_validate_reports_tracked_repository_candidates(capsys: pytest.CaptureFixture[str]) -> None:
    main(["validate"])

    report = json.loads(capsys.readouterr().out)
    assert report["repositories"] == 5331
    assert report["unique_revisions"] == 5331
    assert report["languages"] == {
        "Java": 739,
        "JavaScript": 1007,
        "Python": 1803,
        "TypeScript": 1782,
    }
