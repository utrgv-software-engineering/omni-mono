"""Unit tests for ``governance._exec_in_sandbox`` — the re-exec into the
omni-sandbox CLI.

These drive the arg-building directly with fake policy configs and a
monkeypatched ``os.execvp``, asserting two properties:

* every flag emitted is one the Rust CLI actually accepts (clap hard-fails
  on unknown flags, which would break sandboxing entirely), and
* policy RESTRICTIONS the process sandbox cannot enforce fail closed
  (disk quota, host-filtered network allowlist) while RELAXATIONS it cannot
  honor are ignored with a note (running stricter is safe).
"""

import pytest

from omni_code import governance


# Flags accepted by `omni-sandbox exec` (see src/main.rs ExecArgs in the
# standalone omni-sandbox repo, formerly sandbox-cli/ here). `--net-allow`
# exists but is a hard error by design, so it is deliberately NOT in this set.
SUPPORTED_FLAGS = {
    "--workspace",
    "--ro-bind",
    "--rw-bind",
    "--net",
    "--cwd",
    "--protect",
    "--no-protected-subpaths",
    "--pids-limit",
}

FLAGS_WITH_VALUE = {
    "--workspace",
    "--ro-bind",
    "--rw-bind",
    "--cwd",
    "--protect",
    "--pids-limit",
}


def _config(**overrides):
    cfg = {
        "backend": "bwrap",
        "network_targets": [],
        "resource_limits": {},
        "network_mode": "allowlist",
        "image_spec": {},
        "security_profile": {},
    }
    cfg.update(overrides)
    return cfg


def _run(monkeypatch, config):
    """Call _exec_in_sandbox with execvp captured; return the argv it built."""
    captured = {}

    def fake_execvp(binary, args):
        captured["binary"] = binary
        captured["args"] = list(args)

    # The function sets OMNI_SANDBOXED=1 in os.environ; seed it via
    # monkeypatch so teardown restores the original state.
    monkeypatch.setenv("OMNI_SANDBOXED", "0")
    monkeypatch.setattr(governance.os, "execvp", fake_execvp)
    monkeypatch.setattr(
        governance.sys, "argv", ["/nonexistent-venv/bin/omni", "serve"]
    )
    governance._exec_in_sandbox("/fake/bin/omni-sandbox", config)
    assert "args" in captured, "execvp should have been invoked"
    return captured["args"]


def _flag_values(args, flag):
    return [args[i + 1] for i, a in enumerate(args) if a == flag]


def _sandbox_flags(args):
    """The flag tokens between `exec` and the `--` separator."""
    own = args[2 : args.index("--")]
    flags = []
    i = 0
    while i < len(own):
        tok = own[i]
        assert tok.startswith("--"), f"expected a flag, got positional {tok!r}"
        flags.append(tok)
        i += 2 if tok in FLAGS_WITH_VALUE else 1
    return flags


# ---------------------------------------------------------------------------
# Arg shape / only-supported-flags guard
# ---------------------------------------------------------------------------


def test_basic_invocation_shape(monkeypatch):
    args = _run(monkeypatch, _config())
    assert args[0] == "/fake/bin/omni-sandbox"
    assert args[1] == "exec"
    assert _flag_values(args, "--workspace") == [governance.os.getcwd()]
    # Command tail: same omni entrypoint plus original argv.
    tail = args[args.index("--") + 1 :]
    assert tail == ["/nonexistent-venv/bin/omni", "serve"]


def test_only_supported_flags_are_emitted(monkeypatch):
    # A maximal policy exercising every arg-building branch that still runs.
    config = _config(
        network_mode="open",
        resource_limits={"pids_limit": 256},
        security_profile={
            "seccomp_enabled": False,
            "read_only_root": False,
            "protected_subpaths": [".git", ".env"],
        },
    )
    args = _run(monkeypatch, config)
    unsupported = set(_sandbox_flags(args)) - SUPPORTED_FLAGS
    assert not unsupported, (
        f"flags the Rust CLI would reject (clap hard-fail): {unsupported}"
    )


# ---------------------------------------------------------------------------
# --protect: repeated flag per subpath, never comma-joined
# ---------------------------------------------------------------------------


def test_protect_emitted_once_per_subpath(monkeypatch):
    config = _config(
        security_profile={"protected_subpaths": [".git", ".env", "secrets"]}
    )
    args = _run(monkeypatch, config)
    assert _flag_values(args, "--protect") == [".git", ".env", "secrets"]


def test_protect_values_never_comma_joined(monkeypatch):
    config = _config(security_profile={"protected_subpaths": [".git", ".env"]})
    args = _run(monkeypatch, config)
    for value in _flag_values(args, "--protect"):
        assert "," not in value, (
            "comma-joined --protect would be parsed as ONE bogus subpath name"
        )


def test_no_protect_flag_when_policy_has_none(monkeypatch):
    args = _run(monkeypatch, _config())
    assert "--protect" not in args


# ---------------------------------------------------------------------------
# pids_limit: supported by the CLI, passed through
# ---------------------------------------------------------------------------


def test_pids_limit_passed_through(monkeypatch):
    config = _config(resource_limits={"pids_limit": 256})
    args = _run(monkeypatch, config)
    assert _flag_values(args, "--pids-limit") == ["256"]


def test_no_pids_limit_flag_when_unset(monkeypatch):
    args = _run(monkeypatch, _config())
    assert "--pids-limit" not in args


# ---------------------------------------------------------------------------
# disk_gb: unenforceable restriction — fail closed
# ---------------------------------------------------------------------------


def test_disk_limit_fails_closed(monkeypatch, capsys):
    config = _config(resource_limits={"disk_gb": 10})

    def fail_execvp(binary, args):  # pragma: no cover - must not be reached
        raise AssertionError("execvp must not run when disk_gb is set")

    monkeypatch.setenv("OMNI_SANDBOXED", "0")
    monkeypatch.setattr(governance.os, "execvp", fail_execvp)
    monkeypatch.setattr(
        governance.sys, "argv", ["/nonexistent-venv/bin/omni", "serve"]
    )
    with pytest.raises(SystemExit) as exc:
        governance._exec_in_sandbox("/fake/bin/omni-sandbox", config)
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "disk limit" in err
    assert "cannot enforce disk quotas" in err
    assert "OMNI_SANDBOX_BACKEND=docker or podman" in err


def test_disk_limit_never_emits_disk_flag(monkeypatch):
    # Regression: the old code emitted `--disk-limit 10G`, which clap rejects.
    config = _config(resource_limits={"disk_gb": 10})
    monkeypatch.setenv("OMNI_SANDBOXED", "0")
    monkeypatch.setattr(governance.os, "execvp", lambda *a: None)
    with pytest.raises(SystemExit):
        governance._exec_in_sandbox("/fake/bin/omni-sandbox", config)


# ---------------------------------------------------------------------------
# Security-profile relaxations: ignored (stricter is safe) with a note
# ---------------------------------------------------------------------------


def test_relaxations_not_emitted_as_flags(monkeypatch):
    config = _config(
        security_profile={"seccomp_enabled": False, "read_only_root": False}
    )
    args = _run(monkeypatch, config)
    assert "--no-seccomp" not in args, "clap would hard-fail on --no-seccomp"
    assert "--rw-root" not in args, "clap would hard-fail on --rw-root"


def test_relaxations_logged_to_stderr(monkeypatch, capsys):
    config = _config(
        security_profile={"seccomp_enabled": False, "read_only_root": False}
    )
    _run(monkeypatch, config)
    err = capsys.readouterr().err
    assert "ignores security-profile relaxations" in err
    assert "seccomp_enabled=false" in err
    assert "read_only_root=false" in err


def test_no_relaxation_note_for_default_security_profile(monkeypatch, capsys):
    _run(monkeypatch, _config())
    err = capsys.readouterr().err
    assert "relaxations" not in err


def test_enabled_security_settings_emit_nothing(monkeypatch, capsys):
    # Explicit True (the strict defaults) should neither emit flags nor log.
    config = _config(
        security_profile={"seccomp_enabled": True, "read_only_root": True}
    )
    args = _run(monkeypatch, config)
    assert "--no-seccomp" not in args and "--rw-root" not in args
    assert "relaxations" not in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Network policy (regression coverage for the existing fail-closed branch)
# ---------------------------------------------------------------------------


def test_network_allowlist_fails_closed(monkeypatch, capsys):
    config = _config(network_targets=[{"hostname": "example.com"}])
    monkeypatch.setenv("OMNI_SANDBOXED", "0")
    monkeypatch.setattr(governance.os, "execvp", lambda *a: None)
    with pytest.raises(SystemExit) as exc:
        governance._exec_in_sandbox("/fake/bin/omni-sandbox", config)
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "host-filtered network" in err
    assert "example.com" in err


def test_open_network_mode_adds_net(monkeypatch):
    args = _run(monkeypatch, _config(network_mode="open"))
    assert "--net" in args


def test_default_network_mode_blocks_net(monkeypatch):
    args = _run(monkeypatch, _config())
    assert "--net" not in args
    assert "--net-allow" not in args
