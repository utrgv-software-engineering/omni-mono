"""Docker-image gate for the sandbox-substrate suites (tkt_k5Yk_JBkrIw6).

The two ``*_sandbox_substrate`` modules only mean anything when they run
against a real container, so they skip when the image is absent — which
keeps developers without Docker unaffected but also made the suites
invisible in CI, where the image was never pulled and *both* modules
skipped silently from the day they were added.

The skip is therefore conditional on intent rather than unconditional.
``OMNIAGENTS_REQUIRE_SANDBOX_IMAGE=1`` (set by the ``sandbox-substrate``
job in ``.github/workflows/tests.yml``) turns a missing image from a skip
into a collection error, so a workflow that stops pulling the image — or
a runner without a Docker daemon — fails the build instead of quietly
reporting a green run over zero executed tests. A gate nobody can see
fail is not a gate.
"""

from __future__ import annotations

import os
import shutil
import subprocess

#: Env var that promotes "image missing" from a skip to a hard failure.
REQUIRE_ENV = "OMNIAGENTS_REQUIRE_SANDBOX_IMAGE"


class SandboxImageMissing(RuntimeError):
    """Raised at collection time when CI demanded the image and it is gone."""


def image_available(image: str) -> bool:
    """Whether *image* is already present locally.

    Never pulls: a pull inside the test run would hide the difference
    between "CI provisioned this" and "this machine happened to have it".
    """
    present = _probe(image)
    if not present and os.environ.get(REQUIRE_ENV, "").strip() not in ("", "0"):
        raise SandboxImageMissing(
            f"{REQUIRE_ENV} is set, so this suite must execute, but the docker "
            f"image {image!r} is not available locally. Pull it before running "
            f"(`docker pull {image}`) or unset {REQUIRE_ENV} to allow the skip."
        )
    return present


def _probe(image: str) -> bool:
    if shutil.which("docker") is None:
        return False
    probe = subprocess.run(
        ["docker", "image", "inspect", image], capture_output=True, check=False
    )
    return probe.returncode == 0
