"""Drives a workflow run: figures out which steps are ready, launches
them as Kubernetes Jobs, polls until they finish, unlocks dependents.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from . import k8s_client
from .models import StepDef, WorkflowDef, WorkflowRun, StepRunState
from .store import store

POLL_INTERVAL_SECONDS = 2


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ready_steps(workflow: WorkflowDef, run: WorkflowRun) -> list[StepDef]:
    """Steps that are still pending but whose dependencies have all succeeded."""
    ready = []
    for step in workflow.steps:
        state = run.steps[step.name]
        if state.status != "pending":
            continue
        if all(run.steps[dep].status == "succeeded" for dep in step.depends_on):
            ready.append(step)
    return ready


async def start_workflow_run(workflow: WorkflowDef) -> WorkflowRun:
    run_id = str(uuid.uuid4())
    steps = {step.name: StepRunState(name=step.name) for step in workflow.steps}
    run = WorkflowRun(
        id=run_id,
        workflow_name=workflow.name,
        steps=steps,
        created_at=_now(),
    )
    store.save(run)

    k8s_client.ensure_namespace()
    asyncio.create_task(_drive_run(workflow, run))
    return run


async def _drive_run(workflow: WorkflowDef, run: WorkflowRun) -> None:
    while True:
        for step in ready_steps(workflow, run):
            state = run.steps[step.name]
            state.status = "launching"
            store.save(run)
            state.job_name = k8s_client.create_job_for_step(run.id, step)
            state.status = "running"
            state.started_at = _now()
            store.save(run)

        any_running = False
        for state in run.steps.values():
            if state.status == "running" and state.job_name:
                any_running = True
                k8s_status = k8s_client.get_job_status(state.job_name)
                if k8s_status in ("succeeded", "failed"):
                    state.status = k8s_status
                    state.finished_at = _now()
                    state.logs = k8s_client.get_pod_logs(state.job_name)
                    store.save(run)

        statuses = [s.status for s in run.steps.values()]
        if any(s == "failed" for s in statuses):
            run.status = "failed"
            store.save(run)
            return
        if all(s == "succeeded" for s in statuses):
            run.status = "succeeded"
            store.save(run)
            return
        if not any_running and not ready_steps(workflow, run):
            # Nothing running and nothing ready, but not everything
            # succeeded either - a cycle in depends_on, or a typo'd
            # dependency name. Fail loudly instead of hanging forever.
            run.status = "failed"
            store.save(run)
            return

        await asyncio.sleep(POLL_INTERVAL_SECONDS)
