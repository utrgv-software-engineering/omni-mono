"""A sandbox profile's ``build:`` steps, built into an image once.

``init:`` runs in every container a profile starts. Installing a
toolchain there (apt packages, a browser, CLIs) makes every start pay for
it: every evaluation scenario, every new environment. ``build:`` holds
those steps instead; they are run once, as ``RUN`` steps on the profile's
``options.image``, and the result is tagged by the image and the steps
(``omniagents-profile:<digest>``). Every container the profile starts uses
that image, and a changed step builds a new one. ``init:`` keeps the
per-start work (anything that needs the workspace, credentials or the
running container).
"""

from __future__ import annotations

import asyncio
import copy
import dataclasses
import fcntl
import hashlib
import io
import json
import tarfile
from pathlib import Path
from typing import Any, List

from .profiles import ResolvedProfile

__all__ = ["built_image_tag", "prepare_profile_image"]

_REPOSITORY = "omniagents-profile"


def built_image_tag(base_image: str, build: List[str]) -> str:
    """The image a profile's ``build:`` steps produce on ``base_image``."""
    digest = hashlib.sha256(
        json.dumps({"image": base_image, "build": build}).encode()
    ).hexdigest()[:16]
    return f"{_REPOSITORY}:{digest}"


async def prepare_profile_image(profile: ResolvedProfile) -> ResolvedProfile:
    """``profile`` starting from its built image (built now if missing).

    A profile without ``build:`` is returned unchanged.
    """
    if not profile.build:
        return profile
    client_type = (profile.raw.get("client") or {}).get("type")
    if client_type != "docker":
        raise ValueError(
            f"sandbox `build:` needs a docker profile (client.type is {client_type!r})"
        )
    base = (profile.raw.get("options") or {}).get("image")
    if not isinstance(base, str) or not base.strip():
        raise ValueError("sandbox `build:` needs options.image to build on")
    tag = built_image_tag(base, profile.build)
    await asyncio.to_thread(_ensure_image, tag, base, profile.build)
    raw = copy.deepcopy(profile.raw)
    raw.setdefault("options", {})["image"] = tag
    return dataclasses.replace(profile, raw=raw)


def _ensure_image(tag: str, base: str, build: List[str]) -> None:
    import docker  # type: ignore
    from docker.errors import ImageNotFound  # type: ignore

    client = docker.from_env()
    if _has_image(client, tag, ImageNotFound):
        return
    # One build per tag at a time across processes (parallel scenarios,
    # several environments starting): the others wait, then find it.
    locks = Path.home() / ".cache" / "omniagents" / "sandbox-image-builds"
    locks.mkdir(parents=True, exist_ok=True)
    with open(locks / f"{tag.replace(':', '_')}.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if _has_image(client, tag, ImageNotFound):
            return
        _build(client, tag, base, build)


def _has_image(client: Any, tag: str, not_found: type) -> bool:
    try:
        client.images.get(tag)
        return True
    except not_found:
        return False


def _dockerfile(base: str, build: List[str]) -> str:
    # The JSON form hands each step to `sh -lc` verbatim, as `init:` steps
    # are run, with no Dockerfile quoting rules in between.
    lines = [f"FROM {base}"]
    lines += [f"RUN {json.dumps(['sh', '-lc', step])}" for step in build]
    return "\n".join(lines) + "\n"


def _build(client: Any, tag: str, base: str, build: List[str]) -> None:
    dockerfile = _dockerfile(base, build).encode()
    context = io.BytesIO()
    with tarfile.open(fileobj=context, mode="w") as archive:
        info = tarfile.TarInfo("Dockerfile")
        info.size = len(dockerfile)
        archive.addfile(info, io.BytesIO(dockerfile))
    context.seek(0)
    log: List[str] = []
    for chunk in client.api.build(
        fileobj=context,
        custom_context=True,
        tag=tag,
        rm=True,
        forcerm=True,
        decode=True,
        labels={"org.omniagents.profile-build": tag},
    ):
        if "stream" in chunk:
            log.append(str(chunk["stream"]))
        if "error" in chunk:
            tail = "".join(log)[-4000:]
            raise RuntimeError(
                f"building {tag} from {base} failed: {chunk['error']}\n{tail}"
            )
