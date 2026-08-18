"""Describe the next operation for a staged guideline collection."""

from dataclasses import dataclass
from enum import StrEnum


class GuidelineWorkflowStage(StrEnum):
    """Observable stage of one guideline collection."""

    PROVISIONAL_SCREENING = "provisional_screening"
    NEEDS_LICENSE_REVIEW = "needs_license_review"
    NEEDS_REPLENISHMENT = "needs_replenishment"
    NEEDS_FILE_REVIEW = "needs_file_review"
    READY_TO_FINALIZE = "ready_to_finalize"


@dataclass(frozen=True, slots=True)
class GuidelineWorkflowStatus:
    """Current stage and the single next operation it permits."""

    stage: GuidelineWorkflowStage
    next_action: str
    ready_for_license_review: bool = False
    manual_review_ready: bool = False
    ready_to_finalize: bool = False


def collection_workflow_status(
    *,
    license_policy_applied: bool,
    target_reached: bool,
    human_target_reached: bool,
) -> GuidelineWorkflowStatus:
    """Return the workflow stage derived from durable collection facts."""
    if not license_policy_applied:
        return _provisional_status(target_reached=target_reached)
    if human_target_reached:
        return GuidelineWorkflowStatus(
            GuidelineWorkflowStage.READY_TO_FINALIZE,
            "finalize the guideline collection",
            manual_review_ready=True,
            ready_to_finalize=True,
        )
    if not target_reached:
        return GuidelineWorkflowStatus(
            GuidelineWorkflowStage.NEEDS_REPLENISHMENT,
            "resume eligible repository screening",
        )
    return GuidelineWorkflowStatus(
        GuidelineWorkflowStage.NEEDS_FILE_REVIEW,
        "complete the exported file review checklist",
        manual_review_ready=True,
    )


def _provisional_status(*, target_reached: bool) -> GuidelineWorkflowStatus:
    if target_reached:
        return GuidelineWorkflowStatus(
            GuidelineWorkflowStage.NEEDS_LICENSE_REVIEW,
            "prepare and apply the license allowlist",
            ready_for_license_review=True,
        )
    return GuidelineWorkflowStatus(
        GuidelineWorkflowStage.PROVISIONAL_SCREENING,
        "resume provisional repository screening",
    )
