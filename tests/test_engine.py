"""Scheduling rules: which steps may start, and when is a run done.

These run without a cluster - that is the reason app/engine.py is free of
Kubernetes calls.
"""
from app.engine import overall_phase, ready_steps
from app.models import (
    PHASE_FAILED,
    PHASE_RUNNING,
    PHASE_SUCCEEDED,
    StepDef,
    StepRunState,
    WorkflowDef,
    WorkflowRun,
)


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

    run.steps["a"].phase = PHASE_SUCCEEDED
    assert [s.name for s in ready_steps(workflow, run)] == ["b"]


def test_dependent_step_never_ready_if_dependency_failed():
    workflow = _workflow()
    run = _run_for(workflow)
    run.steps["a"].phase = PHASE_FAILED
    assert ready_steps(workflow, run) == []


def test_running_step_is_not_offered_again():
    """Reconcile runs repeatedly; a launched step must not be launched twice."""
    workflow = _workflow()
    run = _run_for(workflow)
    run.steps["a"].phase = PHASE_RUNNING
    assert ready_steps(workflow, run) == []


def test_independent_steps_are_all_ready_at_once():
    workflow = WorkflowDef(
        name="fan-out",
        steps=[
            StepDef(name="a", image="busybox"),
            StepDef(name="b", image="busybox"),
            StepDef(name="c", image="busybox", depends_on=["a", "b"]),
        ],
    )
    run = _run_for(workflow)
    assert sorted(s.name for s in ready_steps(workflow, run)) == ["a", "b"]

    run.steps["a"].phase = PHASE_SUCCEEDED
    assert [s.name for s in ready_steps(workflow, run)] == ["b"]

    run.steps["b"].phase = PHASE_SUCCEEDED
    assert [s.name for s in ready_steps(workflow, run)] == ["c"]


def test_phase_is_running_while_work_remains():
    workflow = _workflow()
    run = _run_for(workflow)
    assert overall_phase(workflow, run) == PHASE_RUNNING

    run.steps["a"].phase = PHASE_RUNNING
    assert overall_phase(workflow, run) == PHASE_RUNNING


def test_phase_is_succeeded_only_when_every_step_succeeded():
    workflow = _workflow()
    run = _run_for(workflow)
    run.steps["a"].phase = PHASE_SUCCEEDED
    assert overall_phase(workflow, run) == PHASE_RUNNING

    run.steps["b"].phase = PHASE_SUCCEEDED
    assert overall_phase(workflow, run) == PHASE_SUCCEEDED


def test_one_failed_step_fails_the_run_immediately():
    """Even with another step still running: nothing downstream can proceed."""
    workflow = _workflow()
    run = _run_for(workflow)
    run.steps["a"].phase = PHASE_FAILED
    run.steps["b"].phase = PHASE_RUNNING
    assert overall_phase(workflow, run) == PHASE_FAILED


def test_stuck_run_fails_instead_of_hanging():
    """Nothing running, nothing ready, not everything done - fail loudly."""
    workflow = _workflow()
    run = _run_for(workflow)
    # "a" is somehow neither pending nor finished-successfully, so "b"
    # can never become ready. The run must not sit in Running forever.
    run.steps["a"].phase = "Unknown"
    assert overall_phase(workflow, run) == PHASE_FAILED
