#!/usr/bin/env python3
"""Operator recovery after the release process or host stopped mid-activation.

Restores the recorded previous container and manifest; never restores or removes
customer data volumes. The controller remains blocked until this succeeds.
"""

import fcntl
import json
import os
from pathlib import Path
import sys
import time
from factory_release_controller import atomic, docker, http, runtime_url, token

if os.geteuid() != 0:
    raise SystemExit("Run as the deployment operator")
config = json.loads(Path(sys.argv[1]).read_text())
root = Path(config["state_directory"])
with (root / "release.lock").open("w") as lock:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    state = json.loads((root / "state.json").read_text())
    if state.get("phase") not in {"activating", "recovery_required"}:
        raise SystemExit("No interrupted release requires recovery")
    containers = docker("GET", "/containers/json?all=true")
    names = {name.lstrip("/"): c for c in containers for name in c["Names"]}
    name = config["container"]
    previous = state["previous_container"]
    if previous in names:
        if name in names:
            current = docker("GET", "/containers/" + name + "/json")
            if (
                current["Config"].get("Labels", {}).get("omniagents.release")
                != state["revision"]
            ):
                raise SystemExit(
                    "Unexpected container occupies factory name; leave it untouched"
                )
            docker("POST", "/containers/" + name + "/stop?t=45")
            docker(
                "POST",
                "/containers/"
                + name
                + "/rename?name="
                + name
                + "-failed-"
                + str(int(time.time())),
            )
        docker("POST", "/containers/" + previous + "/rename?name=" + name)
    elif name not in names or names[name]["ImageID"] != state["previous_image"]:
        raise SystemExit(
            "Recorded previous container is missing; manual recovery required"
        )
    # Clear candidate pause marker before restarting the retained runtime.
    (root / "pending").unlink(missing_ok=True)
    docker("POST", "/containers/" + name + "/start")
    for _ in range(120):
        try:
            status = http(
                runtime_url(config) + "/integrations/architecture",
                token(config, "runtime_token_file"),
            )
            if status.get("architecture") == config["architecture"] and not status.get(
                "release_paused"
            ):
                break
        except (OSError, ValueError):
            pass
        time.sleep(1)
    else:
        raise SystemExit("Previous runtime is not healthy; recovery remains blocked")
    if state.get("previous_runtime") is None:
        (root / "runtime.json").unlink(missing_ok=True)
    else:
        atomic(root / "runtime.json", state["previous_runtime"])
    state["phase"] = "rolled_back"
    state["recovered_at"] = time.time()
    atomic(root / "state.json", state)
    print("Recovered previous runtime without changing its durable data")
