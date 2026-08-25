"""Talks to the Kubernetes API to run workflow steps as Jobs.

Uses whatever kubeconfig context is currently active - the same one
`kubectl` uses. After `kind create cluster --name tellix`, that's your
local kind cluster, no extra setup needed.
"""
from __future__ import annotations

from kubernetes import client, config as k8s_config

from .models import StepDef

NAMESPACE = "tellix"

_loaded = False


def _load_k8s() -> None:
    global _loaded
    if _loaded:
        return
    try:
        k8s_config.load_kube_config()
    except Exception:
        k8s_config.load_incluster_config()
    _loaded = True


def ensure_namespace() -> None:
    _load_k8s()
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


def create_job_for_step(run_id: str, step: StepDef) -> str:
    _load_k8s()
    batch = client.BatchV1Api()

    job_name = f"tellix-{run_id[:8]}-{step.name}"[:63].rstrip("-")

    container = client.V1Container(
        name=step.name,
        image=step.image,
        command=step.command,
        env=[client.V1EnvVar(name=k, value=v) for k, v in step.env.items()],
    )
    template = client.V1PodTemplateSpec(
        metadata=client.V1ObjectMeta(
            labels={"tellix-run": run_id, "tellix-step": step.name}
        ),
        spec=client.V1PodSpec(containers=[container], restart_policy="Never"),
    )
    spec = client.V1JobSpec(
        template=template,
        backoff_limit=0,
        ttl_seconds_after_finished=300,
        active_deadline_seconds=step.timeout_seconds,
    )
    job = client.V1Job(
        api_version="batch/v1",
        kind="Job",
        metadata=client.V1ObjectMeta(
            name=job_name, labels={"tellix-run": run_id, "tellix-step": step.name}
        ),
        spec=spec,
    )
    batch.create_namespaced_job(namespace=NAMESPACE, body=job)
    return job_name


def get_job_status(job_name: str) -> str:
    _load_k8s()
    batch = client.BatchV1Api()
    job = batch.read_namespaced_job_status(job_name, NAMESPACE)
    if job.status.succeeded:
        return "succeeded"
    if job.status.failed:
        return "failed"
    return "running"


def get_pod_logs(job_name: str) -> str:
    _load_k8s()
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
        # "b'...'" (or "b\"...\"") string instead of clean text. Read
        # the raw HTTP response ourselves and decode it exactly once.
        response = v1.read_namespaced_pod_log(
            pod_name, NAMESPACE, _preload_content=False
        )
        return response.data.decode("utf-8", errors="replace")
    except client.exceptions.ApiException:
        return ""
