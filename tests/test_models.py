"""Validation of submitted workflows.

Every error caught here is an error the user sees at submit time with a
useful message, instead of a run that quietly never makes progress.
"""
import pytest
from pydantic import ValidationError

from app.models import StepDef, WorkflowDef


def _wf(**kwargs) -> dict:
    base = {"name": "wf", "steps": [{"name": "a", "image": "busybox"}]}
    base.update(kwargs)
    return base


def test_step_name_must_follow_kubernetes_naming():
    with pytest.raises(ValidationError, match="lowercase alphanumeric"):
        StepDef(name="Fetch_Data", image="busybox")


def test_workflow_name_must_follow_kubernetes_naming():
    with pytest.raises(ValidationError, match="lowercase alphanumeric"):
        WorkflowDef(**_wf(name="My Workflow"))


def test_workflow_needs_at_least_one_step():
    with pytest.raises(ValidationError):
        WorkflowDef(**_wf(steps=[]))


def test_unknown_dependency_is_rejected():
    with pytest.raises(ValidationError, match="unknown step"):
        WorkflowDef(
            **_wf(
                steps=[
                    {"name": "a", "image": "busybox"},
                    {"name": "b", "image": "busybox", "depends_on": ["typo"]},
                ]
            )
        )


def test_self_dependency_is_rejected():
    with pytest.raises(ValidationError, match="depends on itself"):
        WorkflowDef(
            **_wf(steps=[{"name": "a", "image": "busybox", "depends_on": ["a"]}])
        )


def test_duplicate_step_names_are_rejected():
    with pytest.raises(ValidationError, match="duplicate step names"):
        WorkflowDef(
            **_wf(
                steps=[
                    {"name": "a", "image": "busybox"},
                    {"name": "a", "image": "alpine"},
                ]
            )
        )


def test_dependency_cycle_is_rejected():
    with pytest.raises(ValidationError, match="cycle"):
        WorkflowDef(
            **_wf(
                steps=[
                    {"name": "a", "image": "busybox", "depends_on": ["c"]},
                    {"name": "b", "image": "busybox", "depends_on": ["a"]},
                    {"name": "c", "image": "busybox", "depends_on": ["b"]},
                ]
            )
        )


def test_diamond_shaped_graph_is_fine():
    """Two steps depending on one, both feeding a fourth - not a cycle."""
    workflow = WorkflowDef(
        **_wf(
            steps=[
                {"name": "start", "image": "busybox"},
                {"name": "left", "image": "busybox", "depends_on": ["start"]},
                {"name": "right", "image": "busybox", "depends_on": ["start"]},
                {"name": "join", "image": "busybox", "depends_on": ["left", "right"]},
            ]
        )
    )
    assert len(workflow.steps) == 4


def test_both_snake_case_and_camel_case_are_accepted():
    """Users write depends_on in YAML; Kubernetes stores dependsOn."""
    snake = StepDef(name="b", image="busybox", depends_on=["a"], timeout_seconds=30)
    camel = StepDef.model_validate(
        {"name": "b", "image": "busybox", "dependsOn": ["a"], "timeoutSeconds": 30}
    )
    assert snake == camel


def test_serialising_a_step_produces_kubernetes_style_keys():
    step = StepDef(name="b", image="busybox", depends_on=["a"])
    dumped = step.model_dump(by_alias=True, exclude_none=True)
    assert dumped["dependsOn"] == ["a"]
    assert "depends_on" not in dumped
    # command was never set, so it should not appear at all rather than
    # being sent to Kubernetes as an explicit null.
    assert "command" not in dumped
