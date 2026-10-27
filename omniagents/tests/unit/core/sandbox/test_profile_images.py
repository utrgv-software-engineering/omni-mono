"""A profile's `build:` steps are built into an image once and every start uses it."""

import asyncio
import json
from pathlib import Path

import pytest

from omniagents.core.sandbox import images
from omniagents.core.sandbox.images import (
    _dockerfile,
    built_image_tag,
    prepare_profile_image,
)
from omniagents.core.sandbox.profiles import resolve_profile


def _profile(tmp_path: Path, body: str):
    path = tmp_path / "profile.yml"
    path.write_text(body)
    return resolve_profile(
        workspace_dir=tmp_path, config_dir=tmp_path / "cfg", explicit_path=path
    )


DOCKER = """
client: {type: docker}
options: {image: "node:22-bookworm"}
build:
  - apt-get update && apt-get install -y gh
  - "$(npm root -g)/x install"
init:
  - echo started
"""


def test_build_steps_are_parsed_apart_from_init(tmp_path):
    profile = _profile(tmp_path, DOCKER)
    assert profile.build == [
        "apt-get update && apt-get install -y gh",
        "$(npm root -g)/x install",
    ]
    assert profile.init == ["echo started"]


def test_each_step_is_one_run_handed_to_the_shell_verbatim():
    text = _dockerfile("node:22", ['echo "a b" && x=$(pwd)'])
    first, run = text.splitlines()
    assert first == "FROM node:22"
    assert json.loads(run.removeprefix("RUN ")) == [
        "sh",
        "-lc",
        'echo "a b" && x=$(pwd)',
    ]


def test_the_tag_follows_the_image_and_the_steps():
    tag = built_image_tag("node:22", ["a"])
    assert tag.startswith("omniagents-profile:")
    assert tag == built_image_tag("node:22", ["a"])
    assert tag != built_image_tag("node:22", ["a", "b"])
    assert tag != built_image_tag("node:24", ["a"])


def test_the_profile_starts_from_the_built_image(tmp_path, monkeypatch):
    built = []
    monkeypatch.setattr(
        images, "_ensure_image", lambda tag, base, steps: built.append((tag, base))
    )
    profile = _profile(tmp_path, DOCKER)
    prepared = asyncio.run(prepare_profile_image(profile))
    tag = built_image_tag("node:22-bookworm", profile.build)
    assert built == [(tag, "node:22-bookworm")]
    assert prepared.raw["options"]["image"] == tag
    assert profile.raw["options"]["image"] == "node:22-bookworm"  # not mutated
    assert prepared.init == ["echo started"]


def test_profiles_without_build_are_unchanged(tmp_path):
    profile = _profile(tmp_path, "client: {type: docker}\noptions: {image: x}\n")
    assert asyncio.run(prepare_profile_image(profile)) is profile


def test_build_needs_a_docker_profile(tmp_path):
    profile = _profile(tmp_path, "client: {type: unix_local}\nbuild: [echo hi]\n")
    with pytest.raises(ValueError, match="docker"):
        asyncio.run(prepare_profile_image(profile))


class _Images:
    def __init__(self, present):
        self.present = set(present)

    def get(self, tag):
        from docker.errors import ImageNotFound

        if tag not in self.present:
            raise ImageNotFound(tag)
        return object()


class _Client:
    def __init__(self, present=(), fail=None):
        self.images = _Images(present)
        self.builds = []
        self.api = self
        self._fail = fail

    def build(self, **kwargs):
        self.builds.append(kwargs["tag"])
        if self._fail:
            yield {"stream": "Step 2/2 : RUN ...\n"}
            yield {"error": self._fail}
            return
        self.images.present.add(kwargs["tag"])
        yield {"stream": "Successfully tagged\n"}


def test_an_existing_image_is_not_built_again(tmp_path, monkeypatch):
    import docker

    monkeypatch.setenv("HOME", str(tmp_path))
    client = _Client(present={"omniagents-profile:abc"})
    monkeypatch.setattr(docker, "from_env", lambda: client)
    images._ensure_image("omniagents-profile:abc", "node:22", ["a"])
    assert client.builds == []
    images._ensure_image("omniagents-profile:def", "node:22", ["a"])
    assert client.builds == ["omniagents-profile:def"]


def test_a_failing_step_says_which_and_why(tmp_path, monkeypatch):
    import docker

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(docker, "from_env", lambda: _Client(fail="exit code: 100"))
    with pytest.raises(RuntimeError, match="exit code: 100") as info:
        images._ensure_image("omniagents-profile:abc", "node:22", ["a"])
    assert "Step 2/2" in str(info.value)
