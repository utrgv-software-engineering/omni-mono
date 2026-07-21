"""Tests for omniagents.core.sandbox.container_runtime."""

from __future__ import annotations

import importlib
from types import SimpleNamespace

# The package re-export ``omniagents.core.sandbox.container_runtime``
# (the function) shadows the submodule attribute, so fetch the module
# object explicitly for monkeypatching.
crt = importlib.import_module("omniagents.core.sandbox.container_runtime")

from omniagents.core.sandbox.container_runtime import (
    DEFAULT_RUNTIME_ENV_VAR,
    container_runtime,
    container_runtime_available,
)


class TestContainerRuntime:
    def test_default_is_docker(self, monkeypatch):
        monkeypatch.delenv(DEFAULT_RUNTIME_ENV_VAR, raising=False)
        assert container_runtime() == "docker"

    def test_framework_env_var_selects_podman(self, monkeypatch):
        monkeypatch.setenv(DEFAULT_RUNTIME_ENV_VAR, "podman")
        assert container_runtime() == "podman"

    def test_value_is_normalized(self, monkeypatch):
        monkeypatch.setenv(DEFAULT_RUNTIME_ENV_VAR, "  Podman \n")
        assert container_runtime() == "podman"

    def test_unrecognized_value_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv(DEFAULT_RUNTIME_ENV_VAR, "containerd")
        assert container_runtime() == "docker"

    def test_product_env_var_takes_precedence(self, monkeypatch):
        monkeypatch.setenv("PRODUCT_CONTAINER_RUNTIME", "podman")
        monkeypatch.setenv(DEFAULT_RUNTIME_ENV_VAR, "docker")
        assert (
            container_runtime(
                env_vars=("PRODUCT_CONTAINER_RUNTIME", DEFAULT_RUNTIME_ENV_VAR)
            )
            == "podman"
        )

    def test_unset_product_var_falls_through_to_framework_var(self, monkeypatch):
        monkeypatch.delenv("PRODUCT_CONTAINER_RUNTIME", raising=False)
        monkeypatch.setenv(DEFAULT_RUNTIME_ENV_VAR, "podman")
        assert (
            container_runtime(
                env_vars=("PRODUCT_CONTAINER_RUNTIME", DEFAULT_RUNTIME_ENV_VAR)
            )
            == "podman"
        )

    def test_unrecognized_product_value_falls_through(self, monkeypatch):
        monkeypatch.setenv("PRODUCT_CONTAINER_RUNTIME", "bogus")
        monkeypatch.setenv(DEFAULT_RUNTIME_ENV_VAR, "podman")
        assert (
            container_runtime(
                env_vars=("PRODUCT_CONTAINER_RUNTIME", DEFAULT_RUNTIME_ENV_VAR)
            )
            == "podman"
        )

    def test_custom_default(self, monkeypatch):
        monkeypatch.delenv(DEFAULT_RUNTIME_ENV_VAR, raising=False)
        assert container_runtime(default="podman") == "podman"


class TestContainerRuntimeAvailable:
    def test_available_when_binary_on_path(self, monkeypatch):
        monkeypatch.setenv(DEFAULT_RUNTIME_ENV_VAR, "podman")
        monkeypatch.setattr(
            crt,
            "shutil",
            SimpleNamespace(
                which=lambda name: "/usr/bin/podman" if name == "podman" else None
            ),
        )
        assert container_runtime_available() is True

    def test_unavailable_when_binary_missing(self, monkeypatch):
        monkeypatch.delenv(DEFAULT_RUNTIME_ENV_VAR, raising=False)
        monkeypatch.setattr(crt, "shutil", SimpleNamespace(which=lambda name: None))
        assert container_runtime_available() is False

    def test_probes_the_configured_runtime(self, monkeypatch):
        seen = []
        monkeypatch.setenv(DEFAULT_RUNTIME_ENV_VAR, "podman")
        monkeypatch.setattr(
            crt,
            "shutil",
            SimpleNamespace(which=lambda name: seen.append(name) or "/usr/bin/x"),
        )
        container_runtime_available()
        assert seen == ["podman"]


def test_reexported_from_sandbox_package():
    from omniagents.core.sandbox import (  # noqa: F401
        container_runtime as cr,
        container_runtime_available as cra,
    )
