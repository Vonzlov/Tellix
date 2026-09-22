# Tellix

[![CI](https://github.com/Vonzlov/Tellix/actions/workflows/ci.yml/badge.svg)](https://github.com/Vonzlov/Tellix/actions/workflows/ci.yml)

Kubernetes-native workflow engine - a pet/portfolio project. Define a
workflow as steps with dependencies; each step runs as its own
Kubernetes Job.

## Roadmap

- [x] Phase 0: git/GitHub basics, project scaffolding
- [x] Phase 1: MVP - FastAPI + Kubernetes Jobs
- [x] Phase 2: CRD + kopf operator (Kubernetes-native workflows)
- [ ] Phase 3: MinIO artifacts, web UI, Helm chart
- [ ] Phase 4: Observability + CI/CD + GitOps

## How it works

A workflow is submitted as YAML - named steps, each a container image,
wired together by `depends_on`:

```yaml
name: hello-tellix
steps:
  - name: say-hello
    image: busybox:1.36
    command: ["sh", "-c", "echo 'Hello from step 1!'"]

  - name: say-world
    image: busybox:1.36
    depends_on: ["say-hello"]
    command: ["sh", "-c", "echo 'World!'"]
```

Three moving parts turn that into running containers:

**The CRD** (`k8s/crd-workflowrun.yaml`) teaches the cluster a new object
type, `WorkflowRun`, in the API group `tellix.dev`. After applying it,
`kubectl get workflowruns` works exactly like `kubectl get pods` -
Kubernetes validates these objects against our schema and stores them in
etcd. A CRD is only a schema, though: on its own it makes nothing happen.

**The API** (`app/main.py`) is a thin FastAPI service. `POST
/workflows/runs` validates the workflow and creates a `WorkflowRun`
resource. That is the entire request. It starts no Jobs and remembers
nothing.

**The operator** (`app/operator.py`) is a separate process that watches
`WorkflowRun` resources and reconciles them: launch every step whose
dependencies have succeeded, check the Jobs of steps already running,
write the results back into the resource's `status`. It is built on
[kopf](https://kopf.readthedocs.io/), which is to Kubernetes controllers
roughly what FastAPI is to HTTP servers - you write handlers, it deals
with watching, retries and backoff.

The reason for splitting it up this way is that **the cluster holds all
the state**. Phase 1 kept runs in a Python dict inside the API process,
which meant every restart - including the automatic one `uvicorn
--reload` does when you save a file - silently threw away every run in
flight, while the Kubernetes Jobs they had started kept going, orphaned.
Now a run *is* an object in the cluster. Kill the operator mid-run and
start it again; it reads the resource, sees which Jobs already exist, and
carries on. There is a demo of exactly that below.

## Quickstart

### One-time setup

```
python3.12 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt -r requirements-dev.txt

# local Kubernetes cluster (stays around between runs)
kind create cluster --name tellix

# create the namespace and register the WorkflowRun type
kubectl apply -f k8s/

# the cluster now knows about a type it did not ship with:
kubectl get crd workflowruns.tellix.dev
kubectl get workflowruns -n tellix
```

### Run the tests

No cluster required - the scheduling rules, workflow validation and
manifest building are all pure functions:

```
pytest -v
```

### Start the two processes

Terminal 1 - the operator:

```
PYTHONPATH=. kopf run -m app.operator --namespace tellix --standalone --verbose
```

Terminal 2 - the API:

```
uvicorn app.main:app --reload --reload-dir app --port 8000
```

`--standalone` tells kopf not to look for peer operators (relevant only
when several replicas coordinate who is in charge). `--reload-dir app`
keeps uvicorn's file watcher off the venv and caches.

`PYTHONPATH=.` is needed because `kopf` is a console script installed in
the venv: unlike `python -m`, it puts the venv's `bin/` on `sys.path`
rather than the current directory, so the `app` package would not be
found. Phase 3 makes the project a proper installable package and this
goes away.

### Submit a workflow

Terminal 3:

```
python scripts/submit.py examples/hello-workflow.yaml
python scripts/submit.py examples/ml-pipeline.yaml
```

Then watch it, whichever way you prefer:

```
kubectl get workflowruns -n tellix
kubectl get workflowrun <run-id> -n tellix -o yaml
kubectl describe workflowrun <run-id> -n tellix     # includes operator events
kubectl get jobs,pods -n tellix

curl -s http://localhost:8000/workflows/runs/<run-id> | python3 -m json.tool
```

Deleting a run cleans up after itself - the Jobs it created are its
children via `ownerReferences`, so Kubernetes garbage-collects them:

```
kubectl delete workflowrun <run-id> -n tellix
```

### Demo: state survives the process

This is the thing Phase 2 exists for. Submit the ML pipeline, then kill
the operator while it is still running:

```
python scripts/submit.py examples/ml-pipeline.yaml
# ...in the operator's terminal, hit Ctrl-C mid-run
kubectl get workflowrun <run-id> -n tellix -o yaml   # status is still there
```

The run is untouched, and so are its Jobs. Start the operator again and
its `@kopf.on.resume` handler picks the run up from the resource - a
process with no memory of it whatsoever - and drives it to completion:

```
PYTHONPATH=. kopf run -m app.operator --namespace tellix --standalone --verbose
```

## Continuous integration

Every pull request, and every push to `main`, runs `ruff check` and the
full test suite on GitHub Actions - see `.github/workflows/ci.yml`. The
same two commands run locally:

```
ruff check .
pytest -v
```

Worth running both before pushing: a red check on a pull request takes
a minute to come back, a red terminal takes two seconds.

## Layout

```
app/models.py      pydantic models; workflow validation (names, unknown
                   dependencies, cycles). snake_case in Python,
                   camelCase on the wire, both accepted on input.
app/engine.py      pure scheduling rules: which steps are ready, what
                   phase the run is in. No Kubernetes, no I/O.
app/crd.py         create/read/delete WorkflowRun resources; converts
                   between Kubernetes dicts and models.
app/k8s_client.py  namespace, Job manifests, Job status, pod logs.
app/operator.py    the kopf handlers and the reconcile function.
app/main.py        the FastAPI service.
k8s/               the CRD and namespace manifests.
examples/          two demo workflows.
scripts/submit.py  submit a workflow YAML to the API.
```

## Known limitations

- Steps do not share a filesystem, so nothing can be handed from one
  step to the next yet. `examples/ml-pipeline.yaml` regenerates its
  dataset from a fixed seed instead of reading a file the previous step
  wrote. Artifact passing via MinIO is Phase 3.
- The operator runs on your laptop against the cluster, not inside it.
  Packaging it as a Deployment with its own ServiceAccount and RBAC
  rules is Phase 3, along with a Helm chart.
- Step logs are copied into the run's status, capped at the last 4000
  characters. Kubernetes resources are not a log store; real log
  shipping is Phase 4.
