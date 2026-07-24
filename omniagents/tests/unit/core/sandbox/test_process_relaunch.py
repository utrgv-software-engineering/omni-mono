"""Tests for the governance re-exec helper (``process_relaunch``).

Three concerns:

* mechanics lifted from omni-code's governance — sandbox detection
  (``is_sandboxed``), binary discovery order, editable-install source
  discovery — behave as the product code did;
* the typed relaunch request builds argv byte-identical to what
  omni-code's ``governance._exec_in_sandbox`` produced for the same
  policy configs (parity fixtures below translate a governance-shaped
  config dict into a request exactly as governance now does);
* fail-closed rules hold: unenforceable restrictions (host allowlist,
  disk quota) refuse to run with the pinned message shapes, relaxations
  are noted and ignored, ``pids_limit`` passes through.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from omniagents.core.sandbox import process_relaunch
from omniagents.core.sandbox.process_relaunch import (
    SandboxBind,
    SandboxRelaunchRequest,
    build_relaunch_argv,
    editable_source_dirs,
    find_sandbox_binary,
    is_sandboxed,
    omni_sandbox_exec_argv,
    relaunch_in_sandbox,
)

FAKE_ARGV = ["/nonexistent-venv/bin/omni", "serve"]


@pytest.fixture(autouse=True)
def _no_ambient_binary(monkeypatch):
    monkeypatch.delenv("OMNI_SANDBOX_BIN", raising=False)
    monkeypatch.delenv("OMNI_SANDBOXED", raising=False)
    monkeypatch.setattr(shutil, "which", lambda name: None)


def _executable(path: Path) -> str:
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(0o755)
    return str(path)


# ----------------------------- is_sandboxed ----------------------------- #


class _FakePath:
    """Stands in for Path inside is_sandboxed to control uid_map content."""

    def __init__(self, content: str | None):
        self._content = content

    def __call__(self, *_args):
        return self

    def read_text(self):
        if self._content is None:
            raise OSError("no uid_map")
        return self._content


def test_root_namespace_uid_map_is_not_sandboxed(monkeypatch):
    monkeypatch.setattr(process_relaunch.sys, "platform", "linux")
    monkeypatch.setattr(process_relaunch, "Path", _FakePath("0 0 4294967295"))
    assert is_sandboxed() is False


def test_restricted_uid_map_is_sandboxed(monkeypatch):
    monkeypatch.setattr(process_relaunch.sys, "platform", "linux")
    monkeypatch.setattr(process_relaunch, "Path", _FakePath("0 1000 1"))
    assert is_sandboxed() is True


def test_unreadable_uid_map_falls_back_to_env(monkeypatch):
    monkeypatch.setattr(process_relaunch.sys, "platform", "linux")
    monkeypatch.setattr(process_relaunch, "Path", _FakePath(None))
    assert is_sandboxed() is False
    monkeypatch.setenv("OMNI_SANDBOXED", "1")
    assert is_sandboxed() is True


def test_non_linux_uses_env_var(monkeypatch):
    monkeypatch.setattr(process_relaunch.sys, "platform", "darwin")
    assert is_sandboxed() is False
    monkeypatch.setenv("OMNI_SANDBOXED", "1")
    assert is_sandboxed() is True


def test_env_zero_is_not_sandboxed(monkeypatch):
    monkeypatch.setattr(process_relaunch.sys, "platform", "darwin")
    monkeypatch.setenv("OMNI_SANDBOXED", "0")
    assert is_sandboxed() is False


# -------------------------- binary discovery ---------------------------- #


def test_env_var_wins_over_candidates(monkeypatch, tmp_path):
    env_bin = _executable(tmp_path / "env-omni-sandbox")
    candidate = _executable(tmp_path / "packaged-omni-sandbox")
    monkeypatch.setenv("OMNI_SANDBOX_BIN", env_bin)
    assert find_sandbox_binary([candidate]) == env_bin


def test_non_executable_env_var_is_skipped_not_fatal(monkeypatch, tmp_path):
    bad = tmp_path / "not-executable"
    bad.write_text("nope")
    bad.chmod(0o644)
    candidate = _executable(tmp_path / "packaged-omni-sandbox")
    monkeypatch.setenv("OMNI_SANDBOX_BIN", str(bad))
    assert find_sandbox_binary([candidate]) == candidate


def test_candidates_tried_in_order(tmp_path):
    first = _executable(tmp_path / "assets-omni-sandbox")
    second = _executable(tmp_path / "sibling-omni-sandbox")
    missing = tmp_path / "missing-omni-sandbox"
    assert find_sandbox_binary([missing, first, second]) == first


def test_path_fallback(monkeypatch, tmp_path):
    on_path = _executable(tmp_path / "omni-sandbox")
    monkeypatch.setattr(
        shutil, "which", lambda name: on_path if name == "omni-sandbox" else None
    )
    assert find_sandbox_binary([tmp_path / "missing"]) == on_path


def test_nothing_found_returns_none(tmp_path):
    assert find_sandbox_binary([tmp_path / "missing"]) is None


# ---------------------- editable source discovery ----------------------- #


def _make_venv(tmp_path: Path) -> Path:
    venv = tmp_path / "venv"
    (venv / "lib" / "python3.12" / "site-packages").mkdir(parents=True)
    return venv


def test_editable_pth_and_finder_layouts(tmp_path):
    venv = _make_venv(tmp_path)
    sp = venv / "lib" / "python3.12" / "site-packages"

    legacy_src = tmp_path / "legacy-project"
    legacy_src.mkdir()
    (sp / "legacy.pth").write_text(
        f"# a comment\nimport something\n{legacy_src}\n/does/not/exist\n"
    )

    modern_root = tmp_path / "modern-project"
    modern_pkg = modern_root / "modern_pkg"
    modern_pkg.mkdir(parents=True)
    (sp / "__editable___modern_pkg_1_0_finder.py").write_text(
        f"MAPPING: dict = {{'modern_pkg': {str(modern_pkg)!r}}}\n"
    )

    dirs = editable_source_dirs(venv)
    # .pth binds the literal path; the finder binds the package's PARENT so
    # sibling files (project.yml, pyproject.toml) are visible too.
    assert dirs == [legacy_src, modern_root]


def test_editable_discovery_dedupes_and_handles_missing_lib(tmp_path):
    assert editable_source_dirs(tmp_path / "no-venv") == []

    venv = _make_venv(tmp_path)
    sp = venv / "lib" / "python3.12" / "site-packages"
    src = tmp_path / "proj"
    src.mkdir()
    (sp / "a.pth").write_text(f"{src}\n")
    (sp / "b.pth").write_text(f"{src}\n")
    assert editable_source_dirs(venv) == [src]


# ------------------- argv parity with omni-code governance -------------- #
#
# _request_from_governance_config mirrors exactly how omni-code's
# governance._exec_in_sandbox now translates its resolved policy config
# into a SandboxRelaunchRequest. The expected argv lists are the byte
# output of the PREVIOUS monolithic governance._exec_in_sandbox for the
# same configs (fixture argv points into a nonexistent venv so no
# machine-local venv/editable/config binds leak in).


def _request_from_governance_config(
    config: dict, workspace: str
) -> SandboxRelaunchRequest:
    network_targets = config.get("network_targets", [])
    hosts = [t["hostname"] for t in network_targets if t.get("hostname")]
    limits = config.get("resource_limits", {})
    security = config.get("security_profile", {})
    relaxations = []
    if security.get("seccomp_enabled") is False:
        relaxations.append("seccomp_enabled=false")
    if security.get("read_only_root") is False:
        relaxations.append("read_only_root=false")
    return SandboxRelaunchRequest(
        workspace=workspace,
        network=(
            "unrestricted"
            if not network_targets and config.get("network_mode") == "open"
            else "none"
        ),
        network_allow_hosts=hosts,
        pids_limit=limits.get("pids_limit") or None,
        disk_limit_gb=limits.get("disk_gb") or None,
        ignored_relaxations=relaxations,
        protected_subpaths=security.get("protected_subpaths") or (),
        binds=(),
        backend_label=config.get("backend", "bwrap"),
        argv=FAKE_ARGV,
    )


BIN = "/fake/bin/omni-sandbox"
WS = "/work/space"
TAIL = ["--", "/nonexistent-venv/bin/omni", "serve"]

PARITY_CASES = [
    (
        {
            "backend": "bwrap",
            "network_targets": [],
            "resource_limits": {},
            "network_mode": "allowlist",
            "security_profile": {},
        },
        [BIN, "exec", "--workspace", WS, *TAIL],
    ),
    (
        {
            "backend": "bwrap",
            "network_targets": [],
            "resource_limits": {},
            "network_mode": "open",
            "security_profile": {},
        },
        [BIN, "exec", "--workspace", WS, "--net", *TAIL],
    ),
    (
        {
            "backend": "bwrap",
            "network_targets": [],
            "resource_limits": {"pids_limit": 256},
            "network_mode": "allowlist",
            "security_profile": {},
        },
        [BIN, "exec", "--workspace", WS, "--pids-limit", "256", *TAIL],
    ),
    (
        {
            "backend": "bwrap",
            "network_targets": [],
            "resource_limits": {},
            "network_mode": "allowlist",
            "security_profile": {"protected_subpaths": [".git", ".env", "secrets"]},
        },
        [
            BIN,
            "exec",
            "--workspace",
            WS,
            "--protect",
            ".git",
            "--protect",
            ".env",
            "--protect",
            "secrets",
            *TAIL,
        ],
    ),
    (
        # Maximal enforceable config: every argv-building branch at once.
        {
            "backend": "bwrap",
            "network_targets": [],
            "resource_limits": {"pids_limit": 64},
            "network_mode": "open",
            "security_profile": {
                "seccomp_enabled": False,
                "read_only_root": False,
                "protected_subpaths": [".git"],
            },
        },
        [
            BIN,
            "exec",
            "--workspace",
            WS,
            "--net",
            "--pids-limit",
            "64",
            "--protect",
            ".git",
            *TAIL,
        ],
    ),
    (
        # network_targets without hostnames: no refusal, and no --net even
        # in open mode (governance's elif semantics).
        {
            "backend": "bwrap",
            "network_targets": [{}],
            "resource_limits": {},
            "network_mode": "open",
            "security_profile": {},
        },
        [BIN, "exec", "--workspace", WS, *TAIL],
    ),
]


@pytest.mark.parametrize("config, expected", PARITY_CASES)
def test_argv_parity_with_governance(config, expected):
    request = _request_from_governance_config(config, WS)
    assert build_relaunch_argv(BIN, request) == expected


def test_extra_binds_emitted_in_caller_order(tmp_path):
    ro_dir = tmp_path / "config"
    rw_dir = tmp_path / "home"
    ro_dir.mkdir()
    rw_dir.mkdir()
    request = SandboxRelaunchRequest(
        workspace=WS,
        binds=[SandboxBind(str(ro_dir)), SandboxBind(str(rw_dir), writable=True)],
        argv=FAKE_ARGV,
    )
    args = build_relaunch_argv(BIN, request)
    assert args == [
        BIN,
        "exec",
        "--workspace",
        WS,
        "--ro-bind",
        str(ro_dir),
        "--rw-bind",
        str(rw_dir),
        *TAIL,
    ]


def test_venv_and_editable_binds_discovered(tmp_path):
    venv = _make_venv(tmp_path)
    bin_dir = venv / "bin"
    bin_dir.mkdir()
    entry = _executable(bin_dir / "omni")
    src = tmp_path / "editable-src"
    src.mkdir()
    sp = venv / "lib" / "python3.12" / "site-packages"
    (sp / "proj.pth").write_text(f"{src}\n")

    request = SandboxRelaunchRequest(workspace=WS, argv=[entry, "serve"])
    args = build_relaunch_argv(BIN, request)
    assert args == [
        BIN,
        "exec",
        "--workspace",
        WS,
        "--ro-bind",
        str(venv),
        "--ro-bind",
        str(src),
        "--",
        entry,
        "serve",
    ]


# ----------------------------- fail closed ------------------------------ #


def test_host_allowlist_fails_closed(capsys):
    request = SandboxRelaunchRequest(
        workspace=WS,
        network_allow_hosts=["example.com", "pypi.org"],
        argv=FAKE_ARGV,
    )
    with pytest.raises(SystemExit) as exc:
        build_relaunch_argv(BIN, request)
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "host-filtered network" in err
    assert "example.com, pypi.org" in err
    assert "backend (bwrap) cannot enforce" in err
    assert "OMNI_SANDBOX_BACKEND=docker or podman" in err


def test_disk_limit_fails_closed(capsys):
    request = SandboxRelaunchRequest(workspace=WS, disk_limit_gb=10, argv=FAKE_ARGV)
    with pytest.raises(SystemExit) as exc:
        build_relaunch_argv(BIN, request)
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "disk limit" in err
    assert "(10 GB)" in err
    assert "cannot enforce disk quotas" in err
    assert "OMNI_SANDBOX_BACKEND=docker or podman" in err


def test_backend_label_interpolated(capsys):
    request = SandboxRelaunchRequest(
        workspace=WS, disk_limit_gb=1, backend_label="seatbelt", argv=FAKE_ARGV
    )
    with pytest.raises(SystemExit):
        build_relaunch_argv(BIN, request)
    assert "backend (seatbelt)" in capsys.readouterr().err


def test_relaxations_noted_and_ignored(capsys):
    request = SandboxRelaunchRequest(
        workspace=WS,
        ignored_relaxations=["seccomp_enabled=false", "read_only_root=false"],
        argv=FAKE_ARGV,
    )
    args = build_relaunch_argv(BIN, request)
    assert "--no-seccomp" not in args and "--rw-root" not in args
    err = capsys.readouterr().err
    assert "ignores security-profile relaxations" in err
    assert "seccomp_enabled=false" in err
    assert "read_only_root=false" in err


def test_no_relaxation_note_by_default(capsys):
    build_relaunch_argv(BIN, SandboxRelaunchRequest(workspace=WS, argv=FAKE_ARGV))
    assert "relaxations" not in capsys.readouterr().err


# ------------------------------- relaunch ------------------------------- #


def test_relaunch_sets_marker_and_execs(monkeypatch, capsys):
    captured = {}

    def fake_execvp(binary, args):
        captured["binary"] = binary
        captured["args"] = list(args)

    monkeypatch.setenv("OMNI_SANDBOXED", "0")
    monkeypatch.setattr(process_relaunch.os, "execvp", fake_execvp)
    relaunch_in_sandbox(BIN, SandboxRelaunchRequest(workspace=WS, argv=FAKE_ARGV))
    assert captured["binary"] == BIN
    assert captured["args"][0] == BIN
    assert captured["args"][:4] == [BIN, "exec", "--workspace", WS]
    assert process_relaunch.os.environ["OMNI_SANDBOXED"] == "1"
    assert "enforcing sandbox isolation" in capsys.readouterr().err


# ------------------------- shared argv builder -------------------------- #


def test_builder_never_emits_net_allow():
    args = omni_sandbox_exec_argv(
        BIN,
        WS,
        allow_net=True,
        pids_limit=8,
        protected_subpaths=[".git"],
        binds=[SandboxBind("/a"), SandboxBind("/b", writable=True)],
        command=["/bin/true"],
    )
    assert "--net-allow" not in args
    assert args == [
        BIN,
        "exec",
        "--workspace",
        WS,
        "--net",
        "--pids-limit",
        "8",
        "--protect",
        ".git",
        "--ro-bind",
        "/a",
        "--rw-bind",
        "/b",
        "--",
        "/bin/true",
    ]
