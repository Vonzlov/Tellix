"""The Tellix operator: it makes WorkflowRun resources actually happen.

Run it with:

    kopf run -m app.operator --namespace tellix --standalone --verbose

A CRD teaches the cluster to *store* WorkflowRun objects. Nothing more.
This process is what gives them behaviour: it watches WorkflowRun
resources and repeatedly nudges reality (Kubernetes Jobs) towards what
the resource asks for. That loop is called reconciliation, and the single
most important property of a reconcile function is that running it twice
is the same as running it once - because it will be run again, at
arbitrary times, from arbitrary starting states.

Everything worth knowing about a run lives in the run's own resource in
the cluster, never in this process. Kill the operator mid-run and restart
it: the @kopf.on.resume handler picks the run back up, sees which Jobs
already exist and which have finished, and carries on. That is the whole
point of Phase 2, and it is what Phase 1's in-memory store could not do.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import kopf

from . import crd, engine, k8s_client
from .models import (
    PHASE_FAILED,
    PHASE_RUNNING,
    TERMINAL_PHASES,
)

# How often to re-check an in-flight run. Kubernetes Jobs do not notify
# us directly, so an active run is re-examined on this cadence; finished
# runs are skipped entirely by the `when=` filter below.
RECONCILE_INTERVAL_SECONDS = 3.0

# Pod logs are copied into the run's status so they survive the pod being
# garbage-collected. Kubernetes objects are not a log store, though - a
# single resource is capped at about 1.5 MB - so keep only the tail.
MAX_LOG_CHARS = 4000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _truncate(text: str) -> str:
    if len(text) <= MAX_LOG_CHARS:
        return text
    dropped = len(text) - MAX_LOG_CHARS
    return f"... [{dropped} earlier characters truncated] ...\n{text[-MAX_LOG_CHARS:]}"


def _is_active(status: dict | None, **_) -> bool:
    """Filter: only reconcile runs that have not finished."""
    return (status or {}).get("phase") not in TERMINAL_PHASES


def reconcile(body, patch, log) -> str:
    """Bring one WorkflowRun closer to done. Safe to call any number of times.

    Three passes, in order:
      1. launch every step whose dependencies have succeeded
      2. check the Jobs of steps that are already running
      3. recompute the run's overall phase and write status back

    Nothing here assumes it is the first call, or that the previous call
    finished what it started.
    """
    workflow = crd.workflow_from_cr(body)
    run = crd.run_from_cr(body)

    if run.phase in TERMINAL_PHASES:
        return run.phase

    changed = False

    # 1. Launch ready steps.
    for step in engine.ready_steps(workflow, run):
        state = run.steps[step.name]
        manifest = k8s_client.build_job_manifest(run.id, step)

        # Make the Job a child of this WorkflowRun. Kubernetes then
        # garbage-collects the Job automatically when the run is deleted,
        # so `kubectl delete workflowrun ...` cleans up after itself and
        # there is no orphan-collection code to write.
        kopf.adopt(manifest)

        state.job_name = k8s_client.create_job(manifest)
        state.phase = PHASE_RUNNING
        state.started_at = _now()
        changed = True
        log.info("launched step %r as Job %s", step.name, state.job_name)

    # 2. Check steps that are in flight.
    for state in run.steps.values():
        if state.phase != PHASE_RUNNING or not state.job_name:
            continue

        job_phase = k8s_client.get_job_phase(state.job_name)
        if job_phase is None:
            # The Job vanished - deleted by hand, or TTL-collected while
            # the operator was down. We can never learn its outcome now.
            state.phase = PHASE_FAILED
            state.finished_at = _now()
            state.logs = f"Job {state.job_name} no longer exists; outcome unknown."
            changed = True
            log.warning("step %r lost its Job %s", state.name, state.job_name)
        elif job_phase in TERMINAL_PHASES:
            state.phase = job_phase
            state.finished_at = _now()
            state.logs = _truncate(k8s_client.get_pod_logs(state.job_name))
            changed = True
            log.info("step %r finished: %s", state.name, job_phase)

    # 3. Recompute the run phase and persist everything.
    phase = engine.overall_phase(workflow, run)
    if phase != run.phase:
        changed = True

    if not changed:
        # Writing an identical status on every tick would bump the
        # resource version and generate watch churn for no reason.
        return run.phase

    patch.status["phase"] = phase
    patch.status["steps"] = {
        name: state.model_dump(by_alias=True, exclude_none=True, exclude={"name"})
        for name, state in run.steps.items()
    }
    if run.started_at is None:
        patch.status["startedAt"] = _now()
    if phase in TERMINAL_PHASES:
        patch.status["finishedAt"] = _now()
        log.info("run %s finished: %s", run.id, phase)

    return phase


# --- kopf handlers ----------------------------------------------------
#
# Three different events, one reconcile function. Each handler is a thin
# wrapper so that kopf registers them separately and its logs say which
# cause triggered the work. None of them return a value: a kopf handler's
# return value is stored under status.<handler-id>, and our CRD schema
# does not declare those fields, so Kubernetes would simply prune them.


@kopf.on.create(crd.GROUP, crd.VERSION, crd.PLURAL)
def on_created(body, patch, logger, **_) -> None:
    """A new run was submitted."""
    k8s_client.ensure_namespace()
    reconcile(body, patch, logger)


@kopf.on.resume(crd.GROUP, crd.VERSION, crd.PLURAL, when=_is_active)
def on_resumed(body, patch, logger, **_) -> None:
    """The operator started up and found a run already in progress.

    This handler is the proof that state lives in the cluster: the
    operator has no memory of this run, reads everything it needs from
    the resource, and carries on where the previous process left off.
    """
    logger.info("resuming run %s after operator start", body["metadata"]["name"])
    reconcile(body, patch, logger)


@kopf.timer(
    crd.GROUP,
    crd.VERSION,
    crd.PLURAL,
    interval=RECONCILE_INTERVAL_SECONDS,
    when=_is_active,
)
def on_tick(body, patch, logger, **_) -> None:
    """Periodic re-check of a run that is still in flight."""
    reconcile(body, patch, logger)


@kopf.on.startup()
def configure(settings: kopf.OperatorSettings, **_) -> None:
    # Keep kopf's own bookkeeping out of status. By default it also
    # mirrors handler progress into status.kopf, which our CRD schema
    # does not declare - Kubernetes would prune those writes on every
    # reconcile. Annotations are the modern default anyway.
    settings.persistence.progress_storage = kopf.AnnotationsProgressStorage()

    # Post reconcile decisions as Kubernetes Events on the run itself, so
    # `kubectl describe workflowrun <name>` explains what happened
    # without anyone needing access to the operator's log stream.
    settings.posting.level = logging.INFO
