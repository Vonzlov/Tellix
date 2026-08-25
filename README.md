# Tellix

Kubernetes-native workflow engine - a pet/portfolio project. Define a
workflow as steps with dependencies; each step runs as its own
Kubernetes Job.

## Roadmap

- [x] Phase 0: git/GitHub basics, project scaffolding
- [x] Phase 1: MVP - FastAPI + Kubernetes Jobs
- [ ] Phase 2: CRD + kopf operator (Kubernetes-native workflows)
- [ ] Phase 3: MinIO artifacts, web UI, Helm chart
- [ ] Phase 4: Observability + CI/CD + GitOps

## Phase 1 quickstart

```
# one-time environment setup
python3.12 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt -r requirements-dev.txt

# local Kubernetes cluster (only needed once, stays around between runs)
kind create cluster --name tellix

# run the unit tests (no cluster needed)
pytest -v

# run the API server (keep this terminal open)
uvicorn app.main:app --reload --reload-dir app --port 8000
```

`--reload-dir app` limits the auto-reload watcher to the `app/` source
folder, so unrelated writes elsewhere in the project (pytest cache, venv,
etc.) can't accidentally trigger a restart.

**Important limitation (by design, for now):** run state lives in an
in-memory dict (`app/store.py`), not in Kubernetes. That means **every
restart of this server - whether from `--reload` picking up a file
change, or you stopping it with Ctrl-C and starting it again - wipes
all run history**, even though the underlying Kubernetes Jobs/Pods for
those runs keep going untouched in the cluster. If a run you just
submitted comes back "run not found", the most likely cause is that
this terminal restarted in between. Phase 2 replaces this with
Kubernetes Custom Resources specifically to fix that.

In a second terminal (with the venv activated):

```
python scripts/submit.py examples/hello-workflow.yaml
kubectl get jobs -n tellix
kubectl get pods -n tellix
```
