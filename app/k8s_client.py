"""Talks to the Kubernetes API: namespaces, Jobs, and pod logs.

Uses whatever kubeconfig context is currently active - the same one
`kubectl` uses. After `kind create cluster --name tellix`, that's your
local kind cluster, no extra setup needed.

Job manifests are built as plain dicts by a pure function
(build_job_manifest) that makes no API calls, so the interesting part -
what the Job actually looks like - can be unit-tested without a cluster.
"""
from __future__ import annotations

import hashlib

from kubernetes import client
from kubernetes import config as k8s_config

from .models import PHASE_FAILED, PHASE_RUNNING, PHASE_SUCCEEDED, StepDef

NAMESPACE = "tellix"

# Kubernetes object names are capped at 63 characters (DNS-1123 label).
MAX_NAME_LENGTH = 63

# How long a finished Job (and its pod) sticks around before Kubernetes
# garbage-collects it. The operator reads pod logs when a step finishes,
# so this only needs to outlive a plausible operator outage - an hour is
# generous for a local cluster and keeps `kubectl logs` usable for a
# while after a run completes.
JOB_TTL_SECONDS = 3600

_loaded = False


def load_config() -> None:
    """Load cluster credentials once, from kubeconfig or from in-cluster."""
    global _loaded
    if _loaded:
        return
    try:
        k8s_config.load_kube_config()
    except Exception:
        k8s_config.load_incluster_config()
    _loaded = True


def ensure_namespace() -> None:
    load_config()
    v1 = client.CoreV1Api()
    try:
        v1.read_namespace(NAMESPACE)
    except client.exceptions.ApiException as e:
        if e.status == 404:
            v1.create_namespace(
                client.V1Namespace(metadata=client.V1ObjectMeta(name=NAMESPACE))
            )
        else:
            raise


def job_name_for(run_id: str, step_name: str) -> str:
    """Deterministic Job name for a (run, step) pair.

    Determinism matters more than it looks: it is what makes creating a
    Job idempotent. If the operator dies after creating a Job but before
    recording it in the run's status, the next reconcile computes the
    same name, the API server answers "already exists", and we carry on
    instead of starting the step twice.

    Run ids already carry the workflow name, so the combined name can
    exceed the 63-character limit; when it does, it is truncated and a
    short hash of the full name is appended to keep it unique.
    """
    name = f"{run_id}-{step_name}"
    if len(name) <= MAX_NAME_LENGTH:
        return name
    digest = hashlib.sha1(name.encode()).hexdigest()[:8]
    return f"{name[: MAX_NAME_LENGTH - len(digest) - 1]}-{digest}"


def build_job_manifest(run_id: str, step: StepDef) -> dict:
    """Build the Kubernetes Job that runs one workflow step.

    Pure: no API calls, no cluster needed. The operator adds owner
    references to the returned dict before creating it.
    """
    container: dict = {"name": step.name, "image": step.image}
    if step.command:
        container["command"] = step.command
    if step.env:
        container["env"] = [{"name": k, "value": v} for k, v in step.env.items()]

    labels = {"tellix.dev/run": run_id, "tellix.dev/step": step.name}

    job_spec: dict = {
        # No retries: a failed step should surface as a failed step, not
        # silently succeed on a second attempt.
        "backoffLimit": 0,
        "ttlSecondsAfterFinished": JOB_TTL_SECONDS,
        "template": {
            "metadata": {"labels": labels},
            "spec": {"containers": [container], "restartPolicy": "Never"},
        },
    }
    if step.timeout_seconds:
        job_spec["activeDeadlineSeconds"] = step.timeout_seconds

    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": job_name_for(run_id, step.name),
            "namespace": NAMESPACE,
            "labels": labels,
        },
        "spec": job_spec,
    }


def create_job(manifest: dict) -> str:
    """Create a Job, tolerating one that already exists. Returns its name."""
    load_config()
    batch = client.BatchV1Api()
    namespace = manifest["metadata"].get("namespace", NAMESPACE)
    try:
        batch.create_namespaced_job(namespace=namespace, body=manifest)
    except client.exceptions.ApiException as e:
        # 409 Conflict means a previous reconcile already created this
        # exact Job. That is the expected, harmless case - see
        # job_name_for() on why the name is deterministic.
        if e.status != 409:
            raise
    return manifest["metadata"]["name"]


def get_job_phase(job_name: str) -> str | None:
    """Current phase of a Job, or None if it no longer exists."""
    load_config()
    batch = client.BatchV1Api()
    try:
        job = batch.read_namespaced_job_status(job_name, NAMESPACE)
    except client.exceptions.ApiException as e:
        if e.status == 404:
            return None
        raise
    if job.status.succeeded:
        return PHASE_SUCCEEDED
    if job.status.failed:
        return PHASE_FAILED
    return PHASE_RUNNING


def get_pod_logs(job_name: str) -> str:
    """Logs of the pod a Job created, or an empty string if unavailable."""
    load_config()
    v1 = client.CoreV1Api()
    pods = v1.list_namespaced_pod(NAMESPACE, label_selector=f"job-name={job_name}")
    if not pods.items:
        return ""
    pod_name = pods.items[0].metadata.name
    try:
        # _preload_content=False bypasses the generated client's own
        # response deserialization for this endpoint, which - in some
        # kubernetes-client versions - stringifies the raw bytes via
        # str(data) instead of decoding them, producing a literal
        # "b'...'" string instead of clean text. Read the raw HTTP
        # response ourselves and decode it exactly once.
        response = v1.read_namespaced_pod_log(
            pod_name, NAMESPACE, _preload_content=False
        )
        return response.data.decode("utf-8", errors="replace")
    except client.exceptions.ApiException:
        return ""
