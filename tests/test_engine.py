from app.engine import ready_steps
from app.models import StepDef, StepRunState, WorkflowDef, WorkflowRun


def _run_for(workflow: WorkflowDef) -> WorkflowRun:
    return WorkflowRun(
        id="test",
        workflow_name=workflow.name,
        steps={s.name: StepRunState(name=s.name) for s in workflow.steps},
        created_at="now",
    )


def _workflow() -> WorkflowDef:
    return WorkflowDef(
        name="wf",
        steps=[
            StepDef(name="a", image="busybox"),
            StepDef(name="b", image="busybox", depends_on=["a"]),
        ],
    )


def test_step_with_no_deps_is_ready_immediately():
    workflow = _workflow()
    run = _run_for(workflow)
    assert [s.name for s in ready_steps(workflow, run)] == ["a"]


def test_dependent_step_not_ready_until_dependency_succeeds():
    workflow = _workflow()
    run = _run_for(workflow)
    assert [s.name for s in ready_steps(workflow, run)] == ["a"]

    run.steps["a"].status = "succeeded"
    assert [s.name for s in ready_steps(workflow, run)] == ["b"]


def test_dependent_step_never_ready_if_dependency_failed():
    workflow = _workflow()
    run = _run_for(workflow)
    run.steps["a"].status = "failed"
    assert ready_steps(workflow, run) == []
