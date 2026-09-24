# Tellix

[![CI](https://github.com/Vonzlov/Tellix/actions/workflows/ci.yml/badge.svg)](https://github.com/Vonzlov/Tellix/actions/workflows/ci.yml)

Kubernetes-native workflow engine - a pet/portfolio project. Define a
workflow as steps with dependencies; each step runs as its own
Kubernetes Job.

## Roadmap

- [x] Phase 0: git/GitHub basics, project scaffolding
- [x] Phase 1: MVP - FastAPI + Kubernetes Jobs
- [x] Phase 2: CRD + kopf operator (Kubernetes-native workflows)
- [x] CI: ruff and pytest on every pull request
- [x] Packaging: container image, RBAC, Helm chart, end-to-end CI
- [ ] Artifacts passed between steps (MinIO)
- [ ] Web UI
- [ ] Observability: metrics and structured logs
- [ ] GitOps deployment

## How it works

A workflow is a set of named steps, each a container image, wired
together by dependencies:

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

Three pieces turn that into running containers.

**The CRD** (`charts/tellix/crds/workflowrun.yaml`) teaches the cluster a
new object type, `WorkflowRun`, in the API group `tellix.dev`. After it
is applied, `kubectl get workflowruns` works exactly like `kubectl get
pods` - the API server validates these objects against our schema and
stores them in etcd. A CRD is only a schema, though: on its own it makes
nothing happen.

**The API** (`app/main.py`) is a thin FastAPI service. `POST
/workflows/runs` validates the workflow and creates a `WorkflowRun`
resource. That is the entire request. It starts no Jobs and remembers
nothing - and its ServiceAccount is not permitted to touch a Job even if
it wanted to.

**The operator** (`app/operator.py`) is a separate process that watches
`WorkflowRun` resources and reconciles them: launch every step whose
dependencies have succeeded, check the Jobs of steps already running,
write the results back into the resource's `status`. It is built on
[kopf](https://kopf.readthedocs.io/), which is to Kubernetes controllers
roughly what FastAPI is to HTTP servers.

The reason for splitting it this way is that **the cluster holds all the
state**. Phase 1 kept runs in a Python dict inside the API process, so
every restart silently threw away every run in flight while the Jobs they
had started kept going, orphaned. Now a run *is* an object in the
cluster. Kill the operator mid-run and start it again; it reads the
resource, sees which Jobs already exist, and carries on.

It also means Tellix needs no client of its own:

```
kubectl apply -n tellix -f examples/hello-workflowrun.yaml
```

is a complete way to start a run.

## Install into a cluster

```
# a local cluster, if you do not already have one
kind create cluster --name tellix

# build the image and side-load it, since kind nodes cannot pull from
# your local Docker daemon
docker build -t tellix:dev .
kind load docker-image tellix:dev --name tellix

helm install tellix ./charts/tellix \
  --namespace tellix --create-namespace \
  --set image.tag=dev
```

That installs the CRD, two ServiceAccounts with their own RBAC rules, the
operator and the API. Check it:

```
kubectl get all -n tellix
kubectl apply -n tellix -f examples/hello-workflowrun.yaml
kubectl get workflowruns -n tellix -w
```

To reach the HTTP API from your machine:

```
kubectl port-forward -n tellix svc/tellix-api 8000:8000
curl -s http://localhost:8000/health
```

After changing code, rebuild, reload and restart the pods:

```
docker build -t tellix:dev .
kind load docker-image tellix:dev --name tellix
kubectl rollout restart deployment -n tellix
```

The restart is not optional. The image tag is still `dev`, so from
Kubernetes' point of view nothing about the Deployments changed and it
has no reason to replace the running pods. After changing the chart
itself - RBAC rules, say - run `helm upgrade` with the same arguments as
`helm install` (minus `--create-namespace`) before the restart.

Uninstall with `helm uninstall tellix -n tellix`. The CRD is left behind
on purpose - Helm never deletes CRDs, because doing so would delete every
object of that type along with them.

## Develop from source

Running the two processes on your machine against the cluster is faster
to iterate on than rebuilding an image each time.

```
python3.12 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements-dev.txt
pip install -e .

kind create cluster --name tellix
kubectl create namespace tellix
kubectl apply -f charts/tellix/crds/
```

Terminal 1, the operator:

```
kopf run -m app.operator --namespace tellix --standalone --verbose
```

Terminal 2, the API:

```
uvicorn app.main:app --reload --reload-dir app --port 8000
```

Terminal 3:

```
python scripts/submit.py examples/hello-workflow.yaml
python scripts/submit.py examples/ml-pipeline.yaml
kubectl get workflowruns -n tellix
```

`--standalone` tells kopf not to look for peer operators. `--reload-dir
app` keeps uvicorn's file watcher off the venv and caches. `pip install
-e .` is what makes `app` importable from anywhere, so `kopf run -m
app.operator` no longer needs a `PYTHONPATH` in front of it.

### Demo: state survives the process

Submit the ML pipeline, then kill the operator with Ctrl-C while it is
still running. The run and its Jobs are untouched - they live in the
cluster, not in that process. Start the operator again and its
`@kopf.on.resume` handler picks the run up and drives it to completion.

## Tests and CI

Every pull request runs three jobs: `ruff`, `pytest`, and an end-to-end
job that creates a real kind cluster, installs the chart into it and runs
a workflow through to `Succeeded`. See `.github/workflows/ci.yml`.

Locally:

```
ruff check .
pytest -v
```

Worth running both before pushing: a red check on a pull request takes a
minute to come back, a red terminal takes two seconds.

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
charts/tellix/     Helm chart: the CRD, ServiceAccounts, RBAC, and a
                   Deployment each for the operator and the API.
Dockerfile         one image for both roles.
examples/          demo workflows, as API input and as a raw resource.
scripts/submit.py  submit a workflow YAML to the API.
```

## Known limitations

- Steps do not share a filesystem, so nothing can be handed from one step
  to the next yet. `examples/ml-pipeline.yaml` regenerates its dataset
  from a fixed seed instead of reading a file the previous step wrote.
  Artifact passing via MinIO is next.
- The operator runs as a single replica and does not coordinate with
  peers. Two of them would race over the same runs, which is why the
  chart hard-codes `replicas: 1` rather than exposing it as a value.
- Step logs are copied into the run's status, capped at the last 4000
  characters. Kubernetes resources are not a log store.
- The image is built and side-loaded by hand. Publishing it to a registry
  from CI comes with the GitOps step.
