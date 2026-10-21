"""Contract gate for the reserved serve subcommand (omniagents.product_serve).

Pins the launcher wire contract documented in docs/serve-protocol.md
(protocol version 2):

- the serve argparse surface accepts EXACTLY the argv the launcher
  builds (see launcher/src/main/agent-process.ts, spawn block) — argv
  lists here are constructed literally from that code;
- the readiness payload is field-for-field the launcher's
  SandboxJsonPayload shape, including key order;
- the sandbox-host first-stdout-line handshake is ``{"exec_port": N,
  "host": ...}``;
- ``<prog> describe --json`` reports identity + serve_protocol.

Any change that breaks these tests is a serve-protocol revision: update
docs/serve-protocol.md and SERVE_PROTOCOL_VERSION deliberately.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from omniagents.core.product import reset_product_info
from omniagents.product_serve import (
    SERVE_PROTOCOL_VERSION,
    build_readiness_payload,
    build_serve_parser,
    exec_port_payload,
)


@pytest.fixture(autouse=True)
def _fresh_identity():
    reset_product_info()
    yield
    reset_product_info()


# ---------------------------------------------------------------------------
# Spawn argv — built literally from the launcher's spawn code
# (agent-process.ts: `const args: string[] = ['serve', '--output', 'json']` ...)
# ---------------------------------------------------------------------------


def _launcher_argv_full() -> list[str]:
    """The launcher's argv, every optional branch taken, in the launcher's
    append order. The leading 'serve' word is consumed by product_cli
    dispatch; the parser sees everything after it."""
    args: list[str] = [
        "--output",
        "json",
        "--auth-token",
        "client-secret",
        "--agent-host-control-token",
        "control-secret",
    ]
    # for (const cred of arg.credentials) → one --credential per host
    args += [
        "--credential",
        json.dumps(
            {
                "url": "https://github.com/acme/lib.git",
                "username": "x-access-token",
                "tokenEnv": "GIT_TOKEN_0",
            }
        ),
    ]
    args += ["--snapshot-dir", "/home/u/.config/omni/snapshots"]  # unconditional
    return args


def test_serve_parser_accepts_launcher_argv_full():
    parser = build_serve_parser(prog="omni")
    ns = parser.parse_args(_launcher_argv_full())

    assert ns.output == "json"
    assert ns.auth_token == "client-secret"
    assert ns.agent_host_control_token == "control-secret"
    assert len(ns.credentials) == 1
    assert json.loads(ns.credentials[0])["tokenEnv"] == "GIT_TOKEN_0"
    assert ns.snapshot_dir == Path("/home/u/.config/omni/snapshots")


def test_serve_parser_accepts_launcher_argv_minimal():
    """A targetless host starts without workspace or environment placement."""
    parser = build_serve_parser(prog="omni")
    ns = parser.parse_args(
        [
            "--output",
            "json",
            "--auth-token",
            "client-secret",
            "--agent-host-control-token",
            "control-secret",
            "--snapshot-dir",
            "/home/u/.config/omni/snapshots",
        ]
    )
    assert ns.credentials == []
    # Serve picks its own port — the launcher passes no port flag.
    assert ns.port == 0
    assert ns.auth_token == "client-secret"
    assert ns.agent_host_control_token == "control-secret"


def test_serve_parser_accepts_private_agent_host_control_token():
    parser = build_serve_parser(prog="omni")
    ns = parser.parse_args(
        [
            "--auth-token",
            "client-secret",
            "--agent-host-control-token",
            "control-secret",
        ]
    )

    assert ns.auth_token == "client-secret"
    assert ns.agent_host_control_token == "control-secret"


def test_serve_parser_has_no_workspace_or_port_placement_flags():
    parser = build_serve_parser(prog="omni")
    ns = parser.parse_args(["--output", "json"])
    assert ns.port == 0


# ---------------------------------------------------------------------------
# Readiness payload — the launcher's SandboxJsonPayload, field-for-field
# ---------------------------------------------------------------------------


def test_readiness_payload_shape_byte_match():
    payload = build_readiness_payload(
        host="127.0.0.1",
        port=43210,
        auth_token=None,
        agent_host_id="agenthost-1",
    )

    assert payload == {
        "sandbox_url": "http://127.0.0.1:43210",
        "ws_url": "ws://127.0.0.1:43210/ws",
        "ui_url": "http://127.0.0.1:43210",
        "auth_token": None,
        "ports": {"ui": 43210},
        "agent_host_id": "agenthost-1",
    }
    # Key order is part of the emitted line; pin it.
    assert list(payload.keys()) == [
        "sandbox_url",
        "ws_url",
        "ui_url",
        "auth_token",
        "ports",
        "agent_host_id",
    ]
    # The launcher requires one line containing BOTH sandbox_url and ui_url,
    # parseable as JSON. default=str mirrors the emission path.
    line = json.dumps(payload, default=str)
    parsed = json.loads(line)
    assert "sandbox_url" in parsed and "ui_url" in parsed
    assert "\n" not in line


def test_readiness_payload_auth_token(monkeypatch):
    monkeypatch.delenv("OMNIAGENTS_LEGACY_QUERY_AUTH", raising=False)
    payload = build_readiness_payload(
        host="127.0.0.1",
        port=8123,
        auth_token="s3cret",
        agent_host_id="agenthost-1",
    )
    # The token rides in the dedicated field (Authorization-header /
    # ticket auth); it only appears in ws_url under the legacy flag.
    assert payload["ws_url"] == "ws://127.0.0.1:8123/ws"
    assert payload["auth_token"] == "s3cret"


# ---------------------------------------------------------------------------
# sandbox-host handshake
# ---------------------------------------------------------------------------


def test_sandbox_host_exec_port_line_format():
    payload = exec_port_payload(43999, "127.0.0.1")
    line = json.dumps(payload) + "\n"
    parsed = json.loads(line)
    assert parsed == {"exec_port": 43999, "host": "127.0.0.1"}
    assert isinstance(parsed["exec_port"], int)
    # The launcher parses the FIRST stdout line — one line, newline-terminated.
    assert line.endswith("\n") and line.count("\n") == 1


# ---------------------------------------------------------------------------
# describe
# ---------------------------------------------------------------------------


def test_describe_reports_identity_and_protocol(tmp_path, capsys, monkeypatch):
    from textwrap import dedent

    from omniagents.core.product import bootstrap_product
    from omniagents.product_cli import run_product_describe_command

    manifest = tmp_path / "project.yml"
    manifest.write_text(dedent("""
            name: acme_code
            product:
              name: acme-code
              prog: acme
              label: Acme Code
              slug: acme_code
              update:
                index_url: https://pypi.example.test/acme/acme-code/
                command_hint: acme update
            """))
    bootstrap_product(manifest)

    rc = run_product_describe_command(["--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out.strip())
    assert out["name"] == "acme-code"
    assert out["prog"] == "acme"
    assert out["label"] == "Acme Code"
    assert out["slug"] == "acme_code"
    assert isinstance(out["version"], str) and out["version"]
    assert Path(out["config_dir"]).is_absolute()
    assert out["env_prefix"] == "ACME_CODE"
    assert out["update"] == {
        "index_url": "https://pypi.example.test/acme/acme-code/",
        "command_hint": "acme update",
    }
    assert out["serve_protocol"] == SERVE_PROTOCOL_VERSION == 2


def test_describe_null_update_block(tmp_path, capsys):
    from textwrap import dedent

    from omniagents.core.product import bootstrap_product
    from omniagents.product_cli import run_product_describe_command

    manifest = tmp_path / "project.yml"
    manifest.write_text(dedent("""
            name: bare
            product:
              slug: bare
            """))
    bootstrap_product(manifest)

    assert run_product_describe_command([]) == 0
    out = json.loads(capsys.readouterr().out.strip())
    assert out["update"] is None
    assert out["serve_protocol"] == SERVE_PROTOCOL_VERSION


# ---------------------------------------------------------------------------
# Conventional product profile default
# ---------------------------------------------------------------------------


def test_product_default_profile_sibling_convention(tmp_path):
    """A manifest-sibling sandbox_profile_default.yml is the conventional
    packaged default (how omni-code ships its profile)."""
    from omniagents.core.product import bootstrap_product
    from omniagents.core.sandbox.profiles import product_default_profile_path

    manifest = tmp_path / "project.yml"
    manifest.write_text("name: acme\nproduct:\n  slug: acme\n")
    sibling = tmp_path / "sandbox_profile_default.yml"
    sibling.write_text("client:\n  type: unix_local\n")
    bootstrap_product(manifest)

    assert product_default_profile_path() == sibling


def test_product_default_profile_paths_declaration_wins(tmp_path):
    from omniagents.core.sandbox.profiles import product_default_profile_path

    manifest = tmp_path / "project.yml"
    manifest.write_text(
        "name: acme\nproduct:\n  slug: acme\npaths:\n  sandbox_profile: profiles/base.yml\n"
    )
    declared = tmp_path / "profiles" / "base.yml"
    declared.parent.mkdir()
    declared.write_text("client:\n  type: docker\n")
    # Sibling exists too — the declared path wins.
    (tmp_path / "sandbox_profile_default.yml").write_text("client:\n  type: host\n")

    assert product_default_profile_path(manifest) == declared


def test_product_default_profile_none_without_manifest():
    from omniagents.core.sandbox.profiles import product_default_profile_path

    # Framework identity has no manifest_path → no packaged default layer.
    assert product_default_profile_path() is None


# ---------------------------------------------------------------------------
# Reserved-command mounting
# ---------------------------------------------------------------------------


def test_serve_and_sandbox_host_are_reserved():
    from omniagents.product_cli import RESERVED_COMMANDS, dispatches_to_command

    assert "serve" in RESERVED_COMMANDS
    assert "sandbox-host" in RESERVED_COMMANDS
    assert "describe" in RESERVED_COMMANDS
    assert dispatches_to_command(["serve", "--output", "json"])
    assert dispatches_to_command(["sandbox-host", "--port", "0"])
    assert dispatches_to_command(["describe", "--json"])
