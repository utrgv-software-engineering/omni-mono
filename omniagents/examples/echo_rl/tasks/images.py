"""Build per-task Docker images from Dockerfiles (for dataset-derived tasks).

The bundled corpus uses stock images and needs none of this. Tasks loaded from
OpenThoughts-Agent-v1-RL / echo-rl parquets ship a Dockerfile; we build it once
(cached by content hash) and reuse the resulting tag across rollouts.
"""

from __future__ import annotations

import hashlib
import io
import subprocess


def image_tag_for(dockerfile: str, prefix: str = "echo-rl-task") -> str:
    digest = hashlib.sha1(dockerfile.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}:{digest}"


def image_exists(tag: str) -> bool:
    return (
        subprocess.run(
            ["docker", "image", "inspect", tag],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode
        == 0
    )


def ensure_image(dockerfile: str, *, prefix: str = "echo-rl-task") -> str:
    """Build (if needed) the image for a Dockerfile and return its tag.

    Idempotent: the tag is derived from the Dockerfile contents, so identical
    Dockerfiles share an image and rebuilds are skipped.
    """
    tag = image_tag_for(dockerfile, prefix=prefix)
    if image_exists(tag):
        return tag
    # `docker build -` reads the Dockerfile (and no build context) from stdin.
    proc = subprocess.run(
        ["docker", "build", "-t", tag, "-"],
        input=dockerfile.encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"docker build failed for {tag}:\n{proc.stdout.decode(errors='replace')[-2000:]}"
        )
    return tag
