#!/usr/bin/env python3
"""Submit a workflow YAML file to a running Tellix server.

Usage: python scripts/submit.py examples/hello-workflow.yaml
"""
import json
import sys
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
    with urllib.request.urlopen(req) as resp:
        result = json.loads(resp.read())

    print(json.dumps(result, indent=2))
    print(f"\nRun ID: {result['id']}")
    print(f"Check status: curl {SERVER}/workflows/runs/{result['id']} | python3 -m json.tool")


if __name__ == "__main__":
    main()
