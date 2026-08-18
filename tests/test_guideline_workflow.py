import guideline_workflow


def test_incomplete_provisional_collection_requires_more_screening() -> None:
    status = guideline_workflow.collection_workflow_status(
        license_policy_applied=False,
        target_reached=False,
        human_target_reached=False,
    )

    assert status.stage == guideline_workflow.GuidelineWorkflowStage.PROVISIONAL_SCREENING
    assert status.next_action == "resume provisional repository screening"
    assert status.ready_for_license_review is False
    assert status.manual_review_ready is False


def test_complete_provisional_collection_requires_license_review() -> None:
    status = guideline_workflow.collection_workflow_status(
        license_policy_applied=False,
        target_reached=True,
        human_target_reached=False,
    )

    assert status.stage == guideline_workflow.GuidelineWorkflowStage.NEEDS_LICENSE_REVIEW
    assert status.next_action == "prepare and apply the license allowlist"
    assert status.ready_for_license_review is True
    assert status.manual_review_ready is False


def test_applied_collection_with_pending_repositories_requires_file_review() -> None:
    status = guideline_workflow.collection_workflow_status(
        license_policy_applied=True,
        target_reached=True,
        human_target_reached=False,
    )

    assert status.stage == guideline_workflow.GuidelineWorkflowStage.NEEDS_FILE_REVIEW
    assert status.next_action == "complete the exported file review checklist"
    assert status.ready_for_license_review is False
    assert status.manual_review_ready is True


def test_applied_collection_with_a_deficit_requires_replenishment() -> None:
    status = guideline_workflow.collection_workflow_status(
        license_policy_applied=True,
        target_reached=False,
        human_target_reached=False,
    )

    assert status.stage == guideline_workflow.GuidelineWorkflowStage.NEEDS_REPLENISHMENT
    assert status.next_action == "resume eligible repository screening"
    assert status.manual_review_ready is False


def test_human_complete_collection_is_ready_to_finalize() -> None:
    status = guideline_workflow.collection_workflow_status(
        license_policy_applied=True,
        target_reached=True,
        human_target_reached=True,
    )

    assert status.stage == guideline_workflow.GuidelineWorkflowStage.READY_TO_FINALIZE
    assert status.next_action == "finalize the guideline collection"
    assert status.ready_to_finalize is True
