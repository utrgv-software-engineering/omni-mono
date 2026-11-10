#!/usr/bin/env python3
"""Timer entrypoint: retry an approved idle-deferred request, never a failure."""

import fcntl
import json
from pathlib import Path
import sys
from factory_release_controller import activate

config = json.loads(Path(sys.argv[1]).read_text())
root = Path(config["state_directory"])
with (root / "release.lock").open("w") as lock:
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit(0)
    state = (
        json.loads((root / "state.json").read_text())
        if (root / "state.json").exists()
        else {}
    )
    # Approval deliberately launches the initial attempt. Only busy deferrals
    # retry automatically; failed evals and rollbacks need operator attention.
    if state.get("phase") == "deferred":
        activate(config, json.loads((root / "request.json").read_text()), root)
