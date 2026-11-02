#!/usr/bin/env python3
"""Operator-only approval of an exact, independently checked factory release.

Usage: factory_release_approve.py CONFIG REVISION sha256:DIGEST
CI's restricted SSH command cannot invoke this command or write this file.
"""

import json
import os
from pathlib import Path
import re
import sys
import time

if os.geteuid() != 0:
    raise SystemExit("Run as the deployment operator")
config = json.loads(Path(sys.argv[1]).read_text())
revision, digest = sys.argv[2:]
if not re.fullmatch(r"[0-9a-f]{40}", revision) or not re.fullmatch(
    r"sha256:[0-9a-f]{64}", digest
):
    raise SystemExit("Approve an exact revision and immutable image digest")
state = json.loads((Path(config["state_directory"]) / "state.json").read_text())
if state.get("revision") != revision or state.get("image_digest") != digest:
    raise SystemExit("The approval must match the pending, verified release request")
if state.get("phase") != "awaiting_approval":
    raise SystemExit("This release is not awaiting approval")
path = Path(config["approval_file"])
os.umask(0o077)
temporary = path.with_suffix(".tmp")
temporary.write_text(
    json.dumps(
        {
            "revision": revision,
            "image_digest": digest,
            "approved_at": time.time(),
            "operator": os.environ.get("SUDO_USER", "root"),
        }
    )
)
temporary.replace(path)
print(
    "Approved exact release; the controller must still pass behavior, idle, and health gates"
)
