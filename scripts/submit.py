#!/usr/bin/env python3
"""Submit a workflow YAML file to a running Tellix server.

Usage: python scripts/submit.py examples/hello-workflow.yaml
"""
import json
import sys
import urllib.error
import urllib.request

import yaml

SERVER = "http://localhost:8000"


def main() -> None:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} <workflow.yaml>")
        sys.exit(1)

    with open(sys.argv[1]) as f:
        workflow = yaml.safe_load(f)

    data = json.dumps(workflow).encode()
    req = urllib.request.Request(
        f"{SERVER}/workflows/runs",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            result = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"Server returned {e.code}:\n{body}")
        sys.exit(1)

    print(json.dumps(result, indent=2))
    run_id = result["id"]
    print(f"\nRun ID: {run_id}")
    print("\nWatch it any of these ways:")
    print(f"  curl -s {SERVER}/workflows/runs/{run_id} | python3 -m json.tool")
    print(f"  kubectl get workflowrun {run_id} -n tellix -w")
    print(f"  kubectl describe workflowrun {run_id} -n tellix")


if __name__ == "__main__":
    main()
