"""Minimal in-memory store for workflow runs.

This is intentionally the simplest thing that works for the MVP: a
process-local dict. It disappears when the server restarts, and won't
work across multiple server replicas. Phase 2 replaces this with
Kubernetes Custom Resources as the source of truth (so `kubectl get
workflows` works natively and state survives restarts).
"""
from __future__ import annotations

from .models import WorkflowRun


class InMemoryStore:
    def __init__(self) -> None:
        self._runs: dict[str, WorkflowRun] = {}

    def save(self, run: WorkflowRun) -> None:
        self._runs[run.id] = run

    def get(self, run_id: str) -> WorkflowRun | None:
        return self._runs.get(run_id)

    def list(self) -> list[WorkflowRun]:
        return list(self._runs.values())


store = InMemoryStore()
