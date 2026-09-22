"""The scheduling rules of a workflow run - and nothing else.

Everything here is a pure function of (workflow definition, run state).
No Kubernetes, no network, no clock, no I/O. That is deliberate: this is
the part with the actual logic in it, so it is the part that should be
trivial to unit-test, and tests/test_engine.py runs it without a cluster.

In Phase 1 this module also *drove* runs, with an asyncio loop that
polled Kubernetes and mutated an in-memory store. That driving role now
belongs to the operator (app/operator.py); what stayed behind is the
decision-making.
"""
from __future__ import annotations

from .models import (
    PHASE_FAILED,
    PHASE_PENDING,
    PHASE_RUNNING,
    PHASE_SUCCEEDED,
    StepDef,
    WorkflowDef,
    WorkflowRun,
)


def ready_steps(workflow: WorkflowDef, run: WorkflowRun) -> list[StepDef]:
    """Steps that are still pending but whose dependencies have all succeeded."""
    ready = []
    for step in workflow.steps:
        state = run.steps[step.name]
        if state.phase != PHASE_PENDING:
            continue
        if all(run.steps[dep].phase == PHASE_SUCCEEDED for dep in step.depends_on):
            ready.append(step)
    return ready


def overall_phase(workflow: WorkflowDef, run: WorkflowRun) -> str:
    """Derive the run's phase from the phases of its steps.

    Order matters here: one failed step fails the whole run even if
    others are still going, because nothing downstream of it can ever
    become ready.
    """
    phases = [state.phase for state in run.steps.values()]

    if any(phase == PHASE_FAILED for phase in phases):
        return PHASE_FAILED
    if all(phase == PHASE_SUCCEEDED for phase in phases):
        return PHASE_SUCCEEDED
    if any(phase == PHASE_RUNNING for phase in phases):
        return PHASE_RUNNING
    if ready_steps(workflow, run):
        return PHASE_RUNNING

    # Nothing running, nothing ready, and not everything succeeded. The
    # run cannot make progress on its own. WorkflowDef rejects cycles and
    # unknown dependencies at submit time, so reaching this means state
    # was edited out from under us - fail loudly rather than sit in
    # Running forever.
    return PHASE_FAILED
