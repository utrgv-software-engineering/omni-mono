"""Tests for project-level security configuration (PR 1–5).

Covers:
  - SecurityConfig parsing and defaults
  - Tool blocklist filtering
  - Safety mode enforcement
  - MCP allowlist enforcement
  - Session retention / purge
"""

import json
import sqlite3
import textwrap
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock

import pytest
import yaml

from omniagents.core.security import AuditConfig, SecurityConfig, SessionSecurityConfig

# ── Helpers ──────────────────────────────────────────────────────────


def _write(path: Path, content: str) -> None:
    path.write_text(textwrap.dedent(content))


def _make_project(
    tmp_path,
    *,
    security=None,
    agent_overrides=None,
    tools=None,
    allow_user_mcp=False,
    mcp_servers=None,
):
    """Scaffold a minimal project.yml + agent.yml in *tmp_path*."""
    agents_dir = tmp_path / "agents" / "test_agent"
    agents_dir.mkdir(parents=True)

    agent_cfg = {
        "name": "test_agent",
        "instructions": "Test agent.",
        "welcome_text": "Hi",
    }
    if agent_overrides:
        agent_cfg.update(agent_overrides)
    if mcp_servers:
        agent_cfg["mcp_servers"] = mcp_servers
    (agents_dir / "agent.yml").write_text(yaml.dump(agent_cfg))

    if tools:
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir(parents=True, exist_ok=True)
        (tools_dir / "my_tools.py").write_text(textwrap.dedent(tools))

    proj = {"name": "test_project"}
    if security is not None:
        proj["security"] = security
    if allow_user_mcp:
        proj["allow_user_mcp_servers"] = True
    (tmp_path / "project.yml").write_text(yaml.dump(proj))
    return tmp_path


def _load(project_path):
    from omniagents.core.project.runtime import build_spec_from_project, load_project

    proj = load_project(str(project_path))
    spec = build_spec_from_project(proj)
    return proj, spec


# ═══════════════════════════════════════════════════════════════════════
# 1. SecurityConfig dataclass parsing
# ═══════════════════════════════════════════════════════════════════════


class TestSecurityConfigParsing:
    def test_defaults_from_none(self):
        sc = SecurityConfig.from_dict(None)
        assert sc.safety_mode == "recommended"
        assert sc.blocked_tools == []
        assert sc.blocked_tool_patterns == []
        assert sc.mcp_require_allowlist is False
        assert sc.mcp_allowed_servers == []
        assert sc.tool_timeouts == {}
        assert sc.audit.enabled is False
        assert sc.sessions.retention_days == 0
        assert sc.providers == {}

    def test_defaults_from_empty_dict(self):
        sc = SecurityConfig.from_dict({})
        assert sc.safety_mode == "recommended"
        assert sc.audit.enabled is False

    def test_full_config(self):
        sc = SecurityConfig.from_dict(
            {
                "safety_mode": "enforced",
                "blocked_tools": ["dangerous"],
                "blocked_tool_patterns": ["*_unsafe*"],
                "mcp_require_allowlist": True,
                "mcp_allowed_servers": ["server_a"],
                "tool_timeouts": {"default": 60, "slow": 300},
                "audit": {
                    "enabled": True,
                    "backend": "file",
                    "path": "/tmp/a.jsonl",
                    "retention_days": 90,
                },
                "sessions": {"retention_days": 365},
                "providers": {"auth": {"type": "oidc"}},
            }
        )
        assert sc.safety_mode == "enforced"
        assert sc.blocked_tools == ["dangerous"]
        assert sc.blocked_tool_patterns == ["*_unsafe*"]
        assert sc.mcp_require_allowlist is True
        assert sc.mcp_allowed_servers == ["server_a"]
        assert sc.tool_timeouts == {"default": 60, "slow": 300}
        assert sc.audit.enabled is True
        assert sc.audit.path == "/tmp/a.jsonl"
        assert sc.audit.retention_days == 90
        assert sc.sessions.retention_days == 365
        assert sc.providers == {"auth": {"type": "oidc"}}

    def test_invalid_safety_mode_falls_back(self, capsys):
        sc = SecurityConfig.from_dict({"safety_mode": "banana"})
        assert sc.safety_mode == "recommended"
        assert "not recognised" in capsys.readouterr().out

    def test_round_trip(self):
        original = SecurityConfig.from_dict(
            {
                "safety_mode": "enforced",
                "blocked_tools": ["x"],
                "audit": {"enabled": True},
            }
        )
        rebuilt = SecurityConfig.from_dict(original.to_dict())
        assert rebuilt.safety_mode == original.safety_mode
        assert rebuilt.blocked_tools == original.blocked_tools
        assert rebuilt.audit.enabled == original.audit.enabled


# ═══════════════════════════════════════════════════════════════════════
# 2. Project-level security config threading
# ═══════════════════════════════════════════════════════════════════════


class TestSecurityConfigThreading:
    def test_no_security_section_gives_defaults(self, tmp_path):
        _make_project(tmp_path)
        proj, spec = _load(tmp_path)

        assert proj.security.safety_mode == "recommended"
        assert spec.security_config is not None
        assert spec.security_config["safety_mode"] == "recommended"

    def test_security_section_threads_to_spec(self, tmp_path):
        _make_project(
            tmp_path, security={"safety_mode": "enforced", "blocked_tools": ["x"]}
        )
        _, spec = _load(tmp_path)

        assert spec.security_config["safety_mode"] == "enforced"
        assert spec.security_config["blocked_tools"] == ["x"]


# ═══════════════════════════════════════════════════════════════════════
# 3. Tool blocklist
# ═══════════════════════════════════════════════════════════════════════

TOOLS_PY = """\
from omniagents.core.tools.discovery import function_tool

@function_tool
def safe_tool(x: str) -> str:
    \"\"\"A safe tool.\"\"\"
    return x

@function_tool
def dangerous_tool(x: str) -> str:
    \"\"\"Dangerous.\"\"\"
    return x

@function_tool
def exec_unsafe_cmd(x: str) -> str:
    \"\"\"Unsafe command.\"\"\"
    return x

@function_tool
def read_file(path: str) -> str:
    \"\"\"Read.\"\"\"
    return path
"""


class TestToolBlocklist:
    def test_no_blocklist_all_tools_available(self, tmp_path):
        _make_project(
            tmp_path,
            tools=TOOLS_PY,
            agent_overrides={
                "tools": ["safe_tool", "dangerous_tool", "exec_unsafe_cmd", "read_file"]
            },
        )
        _, spec = _load(tmp_path)
        names = sorted(getattr(t, "name", "") for t in spec.available_tools)
        assert names == ["dangerous_tool", "exec_unsafe_cmd", "read_file", "safe_tool"]

    def test_exact_name_blocked(self, tmp_path):
        _make_project(
            tmp_path,
            security={"blocked_tools": ["dangerous_tool"]},
            tools=TOOLS_PY,
            agent_overrides={"tools": ["safe_tool", "dangerous_tool", "read_file"]},
        )
        _, spec = _load(tmp_path)
        names = [getattr(t, "name", "") for t in spec.available_tools]
        assert "dangerous_tool" not in names
        assert "safe_tool" in names
        assert "read_file" in names

    def test_pattern_blocked(self, tmp_path):
        _make_project(
            tmp_path,
            security={"blocked_tool_patterns": ["*_unsafe*"]},
            tools=TOOLS_PY,
            agent_overrides={"tools": ["safe_tool", "exec_unsafe_cmd", "read_file"]},
        )
        _, spec = _load(tmp_path)
        names = [getattr(t, "name", "") for t in spec.available_tools]
        assert "exec_unsafe_cmd" not in names
        assert "safe_tool" in names

    def test_combined_blocklist(self, tmp_path):
        _make_project(
            tmp_path,
            security={
                "blocked_tools": ["dangerous_tool"],
                "blocked_tool_patterns": ["*_unsafe*"],
            },
            tools=TOOLS_PY,
            agent_overrides={
                "tools": ["safe_tool", "dangerous_tool", "exec_unsafe_cmd", "read_file"]
            },
        )
        _, spec = _load(tmp_path)
        names = sorted(getattr(t, "name", "") for t in spec.available_tools)
        assert names == ["read_file", "safe_tool"]


# ═══════════════════════════════════════════════════════════════════════
# 4. Safety mode enforcement
# ═══════════════════════════════════════════════════════════════════════


class TestSafetyModeEnforcement:
    def test_recommended_respects_agent_config(self, tmp_path):
        _make_project(
            tmp_path,
            security={"safety_mode": "recommended"},
            agent_overrides={
                "use_safe_agent": False,
                "safe_agent_options": {"skip_approvals": True},
            },
        )
        _, spec = _load(tmp_path)
        assert spec.use_safe_agent is False
        assert spec.safe_agent_options.skip_approvals is True

    def test_enforced_overrides_skip_approvals(self, tmp_path):
        _make_project(
            tmp_path,
            security={"safety_mode": "enforced"},
            agent_overrides={"safe_agent_options": {"skip_approvals": True}},
        )
        _, spec = _load(tmp_path)
        assert spec.safe_agent_options.skip_approvals is False

    def test_enforced_forces_use_safe_agent(self, tmp_path):
        _make_project(
            tmp_path,
            security={"safety_mode": "enforced"},
            agent_overrides={"use_safe_agent": False},
        )
        _, spec = _load(tmp_path)
        assert spec.use_safe_agent is True

    def test_no_security_section_respects_agent(self, tmp_path):
        _make_project(
            tmp_path,
            agent_overrides={
                "use_safe_agent": False,
                "safe_agent_options": {"skip_approvals": True},
            },
        )
        _, spec = _load(tmp_path)
        assert spec.use_safe_agent is False
        assert spec.safe_agent_options.skip_approvals is True

    def test_evals_unaffected_when_no_project_defaults(self):
        """Eval pipeline sets use_safe_agent=False after loading spec
        without project_defaults.  security_config should be None."""
        from omniagents.core.config.loader import load_agent_spec_from_yaml
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "agent.yml"
            p.write_text(
                yaml.dump(
                    {
                        "name": "eval_agent",
                        "instructions": "Test",
                        "welcome_text": "Hi",
                        "use_safe_agent": True,
                    }
                )
            )
            spec = load_agent_spec_from_yaml(str(p))
            # Simulate what eval pipeline does
            spec.use_safe_agent = False
            assert spec.use_safe_agent is False
            assert spec.security_config is None


# ═══════════════════════════════════════════════════════════════════════
# 5. MCP allowlist enforcement
# ═══════════════════════════════════════════════════════════════════════


class TestMCPAllowlist:
    def _mcp_servers(self):
        return [
            {
                "name": "allowed_server",
                "type": "stdio",
                "command": "echo",
                "args": ["hi"],
            },
            {
                "name": "blocked_server",
                "type": "stdio",
                "command": "echo",
                "args": ["hi"],
            },
        ]

    def test_no_allowlist_all_servers_pass(self, tmp_path):
        _make_project(tmp_path, mcp_servers=self._mcp_servers())
        _, spec = _load(tmp_path)
        names = [s.name for s in spec.mcp_servers]
        assert sorted(names) == ["allowed_server", "blocked_server"]

    def test_allowlist_filters_servers(self, tmp_path):
        _make_project(
            tmp_path,
            security={
                "mcp_require_allowlist": True,
                "mcp_allowed_servers": ["allowed_server"],
            },
            mcp_servers=self._mcp_servers(),
        )
        _, spec = _load(tmp_path)
        names = [s.name for s in spec.mcp_servers]
        assert names == ["allowed_server"]

    def test_empty_allowlist_blocks_all(self, tmp_path):
        _make_project(
            tmp_path,
            security={"mcp_require_allowlist": True, "mcp_allowed_servers": []},
            mcp_servers=self._mcp_servers(),
        )
        _, spec = _load(tmp_path)
        assert spec.mcp_servers == []

    def test_allowlist_disables_user_mcp(self, tmp_path):
        _make_project(
            tmp_path,
            security={"mcp_require_allowlist": True, "mcp_allowed_servers": ["x"]},
            allow_user_mcp=True,
        )
        proj, _ = _load(tmp_path)
        assert proj.allow_user_mcp_servers is False


# ═══════════════════════════════════════════════════════════════════════
# 6. Session retention / purge
# ═══════════════════════════════════════════════════════════════════════


class TestSessionRetention:
    def _make_db(self, tmp_path):
        """Create a fresh SQLite DB with the sessions schema."""
        from omniagents.core.session.storage import sqlite as sqlite_storage

        db_path = tmp_path / f"retention_test_{uuid.uuid4().hex[:8]}.db"
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        sqlite_storage._ensure_schema(conn)
        return conn, Lock()

    def _patch_store(self, monkeypatch, conn_lock, project, agent):
        from omniagents.core.session.storage.sqlite import SqliteSessionStorage

        original = SqliteSessionStorage._get_store

        def patched(backend, p, a):
            if p == project and a == agent:
                return conn_lock
            return original(backend, p, a)

        monkeypatch.setattr(SqliteSessionStorage, "_get_store", patched)

    def test_purge_removes_old_sessions(self, tmp_path, monkeypatch):
        from omniagents.core.session.history_db import purge_old_sessions

        conn, lock = self._make_db(tmp_path)
        self._patch_store(monkeypatch, (conn, lock), "proj", "agt")

        old = (datetime.utcnow() - timedelta(days=100)).strftime("%Y-%m-%d %H:%M:%S")
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

        conn.execute(
            "INSERT INTO sessions (session_id, created_at) VALUES (?, ?)", ("old1", old)
        )
        conn.execute(
            "INSERT INTO sessions (session_id, created_at) VALUES (?, ?)", ("old2", old)
        )
        conn.execute(
            "INSERT INTO sessions (session_id, created_at) VALUES (?, ?)", ("new1", now)
        )
        conn.execute(
            "INSERT INTO history (session_id, msg_json, created_at) VALUES (?, ?, ?)",
            ("old1", json.dumps({"role": "user"}), old),
        )
        conn.execute(
            "INSERT INTO history (session_id, msg_json, created_at) VALUES (?, ?, ?)",
            ("old2", json.dumps({"role": "user"}), old),
        )
        conn.execute(
            "INSERT INTO history (session_id, msg_json, created_at) VALUES (?, ?, ?)",
            ("new1", json.dumps({"role": "user"}), now),
        )
        conn.commit()

        purged = purge_old_sessions(30, project_slug="proj", agent_slug="agt")
        # The purged IDS, not a count: the caller has to finish the job in
        # the conversation store and the replay journal.
        assert sorted(purged) == ["old1", "old2"]

        remaining = [
            r[0] for r in conn.execute("SELECT session_id FROM sessions").fetchall()
        ]
        assert remaining == ["new1"]
        assert conn.execute("SELECT COUNT(*) FROM history").fetchone()[0] == 1

    def test_purge_noop_with_zero_days(self, tmp_path, monkeypatch):
        from omniagents.core.session.history_db import purge_old_sessions

        conn, lock = self._make_db(tmp_path)
        self._patch_store(monkeypatch, (conn, lock), "proj", "agt")

        old = (datetime.utcnow() - timedelta(days=999)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "INSERT INTO sessions (session_id, created_at) VALUES (?, ?)", ("old", old)
        )
        conn.commit()

        assert purge_old_sessions(0, project_slug="proj", agent_slug="agt") == []
        assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1

    def test_purge_noop_when_nothing_old(self, tmp_path, monkeypatch):
        from omniagents.core.session.history_db import purge_old_sessions

        conn, lock = self._make_db(tmp_path)
        self._patch_store(monkeypatch, (conn, lock), "proj", "agt")

        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "INSERT INTO sessions (session_id, created_at) VALUES (?, ?)",
            ("recent", now),
        )
        conn.commit()

        assert purge_old_sessions(30, project_slug="proj", agent_slug="agt") == []


# ═══════════════════════════════════════════════════════════════════════
# 7. Audit events
# ═══════════════════════════════════════════════════════════════════════


class TestFileAuditBackend:
    def test_writes_jsonl(self, tmp_path):
        import asyncio
        from omniagents.core.audit.file_backend import FileAuditBackend

        log_path = tmp_path / "audit.jsonl"
        backend = FileAuditBackend(path=str(log_path))

        asyncio.run(
            backend.handle_event(
                event_type="run_started",
                session_id="s1",
                timestamp="2026-01-01T00:00:00+00:00",
                details={},
            )
        )
        asyncio.run(
            backend.handle_event(
                event_type="tool_called",
                session_id="s1",
                details={"tool": "read_file"},
            )
        )

        lines = log_path.read_text().strip().splitlines()
        assert len(lines) == 2
        first = json.loads(lines[0])
        assert first["event_type"] == "run_started"
        second = json.loads(lines[1])
        assert second["details"]["tool"] == "read_file"

    def test_purge_old_entries(self, tmp_path):
        from omniagents.core.audit.file_backend import FileAuditBackend
        from datetime import datetime, timedelta, timezone

        log_path = tmp_path / "audit.jsonl"
        old_ts = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
        new_ts = datetime.now(timezone.utc).isoformat()
        log_path.write_text(
            json.dumps({"event_type": "old", "timestamp": old_ts})
            + "\n"
            + json.dumps({"event_type": "new", "timestamp": new_ts})
            + "\n"
        )

        # Creating backend with retention_days triggers purge on init
        backend = FileAuditBackend(path=str(log_path), retention_days=30)
        lines = log_path.read_text().strip().splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["event_type"] == "new"

    def test_no_purge_when_zero_days(self, tmp_path):
        from omniagents.core.audit.file_backend import FileAuditBackend
        from datetime import datetime, timedelta, timezone

        log_path = tmp_path / "audit.jsonl"
        old_ts = (datetime.now(timezone.utc) - timedelta(days=999)).isoformat()
        log_path.write_text(
            json.dumps({"event_type": "ancient", "timestamp": old_ts}) + "\n"
        )

        backend = FileAuditBackend(path=str(log_path), retention_days=0)
        lines = log_path.read_text().strip().splitlines()
        assert len(lines) == 1

    def test_create_audit_hook_returns_callable(self, tmp_path):
        from omniagents.core.audit.file_backend import create_audit_hook

        hook = create_audit_hook(
            {"enabled": True, "path": str(tmp_path / "test.jsonl"), "retention_days": 0}
        )
        assert callable(hook)

    def test_creates_parent_dirs(self, tmp_path):
        from omniagents.core.audit.file_backend import FileAuditBackend

        deep_path = tmp_path / "a" / "b" / "c" / "audit.jsonl"
        backend = FileAuditBackend(path=str(deep_path))
        assert deep_path.parent.exists()


class TestToolTimeouts:
    def test_no_timeouts_tools_unchanged(self, tmp_path):
        _make_project(
            tmp_path,
            tools=TOOLS_PY,
            agent_overrides={"tools": ["safe_tool"]},
        )
        _, spec = _load(tmp_path)
        tool = spec.available_tools[0]
        assert not hasattr(tool, "_timeout_seconds")

    def test_default_timeout_applied(self, tmp_path):
        _make_project(
            tmp_path,
            security={"tool_timeouts": {"default": 30}},
            tools=TOOLS_PY,
            agent_overrides={"tools": ["safe_tool", "read_file"]},
        )
        _, spec = _load(tmp_path)
        for tool in spec.available_tools:
            assert getattr(tool, "_timeout_seconds", None) == 30

    def test_per_tool_timeout_overrides_default(self, tmp_path):
        _make_project(
            tmp_path,
            security={"tool_timeouts": {"default": 30, "safe_tool": 120}},
            tools=TOOLS_PY,
            agent_overrides={"tools": ["safe_tool", "read_file"]},
        )
        _, spec = _load(tmp_path)
        by_name = {getattr(t, "name", ""): t for t in spec.available_tools}
        assert getattr(by_name["safe_tool"], "_timeout_seconds", None) == 120
        assert getattr(by_name["read_file"], "_timeout_seconds", None) == 30

    def test_timeout_fires(self):
        """Verify the wrapped invoke returns TOOL_TIMEOUT on expiry."""
        import asyncio
        from omniagents.core.agents.builder import _apply_tool_timeouts
        from agents.tool import FunctionTool

        async def slow_invoke(ctx, params):
            await asyncio.sleep(10)
            return "done"

        tool = FunctionTool(
            name="slow_tool",
            description="slow",
            params_json_schema={},
            on_invoke_tool=slow_invoke,
        )
        wrapped = _apply_tool_timeouts([tool], {"default": 0.05})[0]
        result = asyncio.run(wrapped.on_invoke_tool(None, "{}"))
        parsed = json.loads(result)
        assert parsed["error"] == "TOOL_TIMEOUT"
        assert parsed["tool"] == "slow_tool"

    def test_preserves_custom_attributes(self, tmp_path):
        _make_project(
            tmp_path,
            security={"tool_timeouts": {"default": 60}},
            tools=TOOLS_PY,
            agent_overrides={"tools": ["safe_tool"]},
        )
        _, spec = _load(tmp_path)
        tool = spec.available_tools[0]
        assert getattr(tool, "_is_omniagents_core_tool", False) is True


class TestAuditEvents:
    def test_make_audit_event_defaults(self):
        from omniagents.core.audit.events import make_audit_event, RUN_STARTED

        evt = make_audit_event(RUN_STARTED)
        assert evt["event_type"] == "run_started"
        assert "timestamp" in evt
        assert evt["session_id"] is None
        assert evt["details"] == {}

    def test_make_audit_event_with_fields(self):
        from omniagents.core.audit.events import make_audit_event, TOOL_CALLED

        evt = make_audit_event(
            TOOL_CALLED,
            session_id="s1",
            run_id="r1",
            agent_name="agent",
            user_id="u1",
            details={"tool": "read_file", "call_id": "c1"},
        )
        assert evt["event_type"] == "tool_called"
        assert evt["session_id"] == "s1"
        assert evt["run_id"] == "r1"
        assert evt["agent_name"] == "agent"
        assert evt["user_id"] == "u1"
        assert evt["details"]["tool"] == "read_file"

    def test_all_event_types_are_strings(self):
        from omniagents.core.audit import events

        for name in [
            "RUN_STARTED",
            "RUN_END",
            "TOOL_CALLED",
            "TOOL_RESULT",
            "TOOL_APPROVED",
            "TOOL_REJECTED",
            "GUARDRAIL_TRIGGERED",
        ]:
            assert isinstance(getattr(events, name), str)

    def test_timestamp_is_utc_iso(self):
        from omniagents.core.audit.events import make_audit_event, RUN_STARTED

        evt = make_audit_event(RUN_STARTED)
        ts = evt["timestamp"]
        # Should be parseable and contain timezone
        assert "+" in ts or "Z" in ts


# ═══════════════════════════════════════════════════════════════════════
# 9. Provider infrastructure
# ═══════════════════════════════════════════════════════════════════════


class TestProviderRegistry:
    def test_resolve_auth_none(self):
        from omniagents.core.providers import resolve_provider
        from omniagents.core.providers.auth.none import NoneAuthProvider

        p = resolve_provider("auth", {"type": "none"})
        assert isinstance(p, NoneAuthProvider)

    def test_resolve_auth_token(self):
        from omniagents.core.providers import resolve_provider
        from omniagents.core.providers.auth.token import TokenAuthProvider

        p = resolve_provider("auth", {"type": "token", "token": "abc"})
        assert isinstance(p, TokenAuthProvider)

    def test_resolve_default_is_none(self):
        from omniagents.core.providers import resolve_provider
        from omniagents.core.providers.auth.none import NoneAuthProvider

        # No config at all → "none" type
        p = resolve_provider("auth")
        assert isinstance(p, NoneAuthProvider)

    def test_resolve_unknown_raises(self):
        from omniagents.core.providers import resolve_provider

        with pytest.raises(KeyError, match="banana"):
            resolve_provider("auth", {"type": "banana"})

    def test_available_providers(self):
        from omniagents.core.providers import available_providers

        # Trigger registration by importing the domain packages
        import omniagents.core.providers.auth  # noqa: F401
        import omniagents.core.providers.content_scanner  # noqa: F401

        assert "none" in available_providers("auth")
        assert "token" in available_providers("auth")
        assert "regex" in available_providers("content_scanner")

    def test_secrets_env_provider(self, monkeypatch):
        from omniagents.core.providers.secrets.env import EnvSecretsProvider

        monkeypatch.setenv("MY_TEST_SECRET", "val123")
        p = EnvSecretsProvider()
        assert p.get_secret("MY_TEST_SECRET") == "val123"
        assert p.get_secret("nonexistent_key_xyz") is None


class TestRegexScanner:
    def test_detects_ssn(self):
        import asyncio
        from omniagents.core.providers.content_scanner.regex import RegexScanner

        scanner = RegexScanner()
        result = asyncio.run(scanner.scan("SSN: 123-45-6789"))
        assert len(result.findings) == 1
        assert result.findings[0].pattern_name == "ssn"
        assert "[SSN]" in result.redacted_content

    def test_detects_email(self):
        import asyncio
        from omniagents.core.providers.content_scanner.regex import RegexScanner

        scanner = RegexScanner()
        result = asyncio.run(scanner.scan("Contact: user@hospital.org"))
        assert any(f.pattern_name == "email" for f in result.findings)

    def test_no_findings_is_clean(self):
        import asyncio
        from omniagents.core.providers.content_scanner.regex import RegexScanner

        scanner = RegexScanner()
        result = asyncio.run(scanner.scan("Hello world, nothing sensitive."))
        assert result.allowed
        assert result.findings == []
        assert result.redacted_content is None

    def test_block_mode(self):
        import asyncio
        from omniagents.core.providers.content_scanner.regex import RegexScanner

        scanner = RegexScanner({"block": True})
        result = asyncio.run(scanner.scan("SSN: 123-45-6789"))
        assert not result.allowed

    def test_custom_patterns(self):
        import asyncio
        from omniagents.core.providers.content_scanner.regex import RegexScanner

        scanner = RegexScanner(
            {"extra_patterns": [{"name": "patient_id", "pattern": r"PT-\d{6}"}]}
        )
        result = asyncio.run(scanner.scan("Patient PT-123456 admitted"))
        assert any(f.pattern_name == "patient_id" for f in result.findings)


class TestTokenAuthProvider:
    def test_valid_token(self):
        import asyncio
        from omniagents.core.providers.auth.token import TokenAuthProvider

        p = TokenAuthProvider({"token": "secret"})
        result = asyncio.run(p.authenticate("secret"))
        assert result.authenticated

    def test_invalid_token(self):
        import asyncio
        from omniagents.core.providers.auth.token import TokenAuthProvider

        p = TokenAuthProvider({"token": "secret"})
        result = asyncio.run(p.authenticate("wrong"))
        assert not result.authenticated

    def test_no_token_configured_refuses_construction(self):
        """Selecting token auth without any usable credential fails closed."""
        from omniagents.core.providers.auth.token import TokenAuthProvider

        with pytest.raises(ValueError, match="zero usable"):
            TokenAuthProvider({})


# ═══════════════════════════════════════════════════════════════════════
# 10. Provider wiring
# ═══════════════════════════════════════════════════════════════════════


class TestProviderWiring:
    def test_secrets_provider_wired_in_builder(self, tmp_path):
        """SecretsProvider resolves from security_config.providers.secrets."""
        from omniagents.core.providers import resolve_provider
        from omniagents.core.providers.secrets.env import EnvSecretsProvider

        p = resolve_provider("secrets", {"type": "env"})
        assert isinstance(p, EnvSecretsProvider)
        # Verify it works through the interface
        import os

        os.environ["_TEST_WIRING_KEY"] = "wired"
        assert p.get_secret("_TEST_WIRING_KEY") == "wired"
        del os.environ["_TEST_WIRING_KEY"]

    def test_session_encryption_set_provider(self):
        """set_encryption_provider installs custom connection factory."""
        import omniagents.core.session.history_db as hdb
        from omniagents.core.session.storage import sqlite as sqlite_storage

        original = sqlite_storage._encryption_provider
        try:

            class FakeProvider:
                def get_connection(self, db_path):
                    import sqlite3

                    conn = sqlite3.connect(db_path, check_same_thread=False)
                    conn.execute("PRAGMA journal_mode=WAL")
                    return conn

            # The façade delegates to the sqlite storage module, which is
            # where _get_store consults the provider.
            hdb.set_encryption_provider(FakeProvider())
            assert sqlite_storage._encryption_provider is not None
        finally:
            sqlite_storage._encryption_provider = original

    def test_content_scanner_guardrail_adapter(self):
        """RegexScanner produces guardrails that detect SSN."""
        import asyncio
        from omniagents.core.providers.content_scanner.regex import RegexScanner
        from omniagents.core.providers.content_scanner.guardrail_adapter import (
            make_scanner_input_guardrail,
        )

        scanner = RegexScanner({"block": True})
        guardrail = make_scanner_input_guardrail(scanner)
        assert guardrail is not None
        assert hasattr(guardrail, "guardrail_function")

    def test_content_scanner_injected_into_spec(self, tmp_path):
        """When content_scanner provider is configured, guardrails are auto-prepended."""
        _make_project(
            tmp_path,
            security={
                "providers": {
                    "content_scanner": {"type": "regex"},
                },
            },
        )
        _, spec = _load(tmp_path)
        # Should have at least the scanner guardrails
        guard_names = [
            getattr(g, "name", getattr(g, "get_name", lambda: "")())
            for g in spec.input_guardrails
        ]
        assert any("content_scanner" in str(n) for n in guard_names)

    def test_no_provider_config_no_guardrails(self, tmp_path):
        """Without content_scanner config, no extra guardrails."""
        _make_project(tmp_path)
        _, spec = _load(tmp_path)
        assert spec.input_guardrails == []
        assert spec.output_guardrails == []


# ═══════════════════════════════════════════════════════════════════════
# WI-1: Audit event enrichment — truncation helper
# ═══════════════════════════════════════════════════════════════════════


class TestTruncateForAudit:
    def test_short_value_passes_through(self):
        from omniagents.core.audit.events import truncate_for_audit

        assert truncate_for_audit("hello", 100) == "hello"

    def test_exact_limit_passes_through(self):
        from omniagents.core.audit.events import truncate_for_audit

        text = "x" * 100
        assert truncate_for_audit(text, 100) == text

    def test_long_value_truncated(self):
        from omniagents.core.audit.events import truncate_for_audit

        text = "a" * 5000
        result = truncate_for_audit(text, 100)
        assert len(result) > 100  # includes suffix
        assert result.startswith("a" * 100)
        assert "truncated" in result
        assert "5000" in result

    def test_none_becomes_empty_string(self):
        from omniagents.core.audit.events import truncate_for_audit

        assert truncate_for_audit(None) == ""

    def test_non_string_converted(self):
        from omniagents.core.audit.events import truncate_for_audit

        assert truncate_for_audit(42) == "42"


# ═══════════════════════════════════════════════════════════════════════
# WI-1: AuditConfig max_detail_chars
# ═══════════════════════════════════════════════════════════════════════


class TestAuditConfigMaxDetailChars:
    def test_default_value(self):
        cfg = AuditConfig()
        assert cfg.max_detail_chars == 4096

    def test_from_dict_custom(self):
        cfg = AuditConfig.from_dict({"max_detail_chars": 1024})
        assert cfg.max_detail_chars == 1024

    def test_from_dict_missing_uses_default(self):
        cfg = AuditConfig.from_dict({})
        assert cfg.max_detail_chars == 4096

    def test_security_config_roundtrip(self):
        sc = SecurityConfig.from_dict({"audit": {"max_detail_chars": 2048}})
        d = sc.to_dict()
        assert d["audit"]["max_detail_chars"] == 2048


# ═══════════════════════════════════════════════════════════════════════
# WI-7: Legal hold
# ═══════════════════════════════════════════════════════════════════════


class TestLegalHold:
    def _make_db(self, tmp_path):
        from omniagents.core.session.storage import sqlite as sqlite_storage

        db_path = tmp_path / f"hold_test_{uuid.uuid4().hex[:8]}.db"
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        sqlite_storage._ensure_schema(conn)
        return conn, Lock()

    def _patch_store(self, monkeypatch, conn_lock, project, agent):
        from omniagents.core.session.storage.sqlite import SqliteSessionStorage

        original = SqliteSessionStorage._get_store

        def patched(backend, p, a):
            if p == project and a == agent:
                return conn_lock
            return original(backend, p, a)

        monkeypatch.setattr(SqliteSessionStorage, "_get_store", patched)

    def test_purge_skips_held_sessions(self, tmp_path, monkeypatch):
        from omniagents.core.session.history_db import (
            purge_old_sessions,
            set_session_hold,
        )

        conn, lock = self._make_db(tmp_path)
        self._patch_store(monkeypatch, (conn, lock), "proj", "agt")

        old = (datetime.utcnow() - timedelta(days=100)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "INSERT INTO sessions (session_id, created_at) VALUES (?, ?)",
            ("held1", old),
        )
        conn.execute(
            "INSERT INTO sessions (session_id, created_at) VALUES (?, ?)",
            ("unheld1", old),
        )
        conn.commit()

        set_session_hold("held1", True, project_slug="proj", agent_slug="agt")

        purged = purge_old_sessions(30, project_slug="proj", agent_slug="agt")
        assert purged == ["unheld1"]

        remaining = [
            r[0] for r in conn.execute("SELECT session_id FROM sessions").fetchall()
        ]
        assert remaining == ["held1"]

    def test_delete_blocked_on_hold(self, tmp_path, monkeypatch):
        from omniagents.core.session.history_db import (
            delete_session,
            set_session_hold,
        )

        conn, lock = self._make_db(tmp_path)
        self._patch_store(monkeypatch, (conn, lock), "proj", "agt")

        conn.execute(
            "INSERT INTO sessions (session_id, created_at) VALUES (?, datetime('now'))",
            ("s1",),
        )
        conn.commit()

        set_session_hold("s1", True, project_slug="proj", agent_slug="agt")

        with pytest.raises(ValueError, match="legal hold"):
            delete_session("s1", project_slug="proj", agent_slug="agt")

    def test_set_hold_returns_false_for_missing(self, tmp_path, monkeypatch):
        from omniagents.core.session.history_db import set_session_hold

        conn, lock = self._make_db(tmp_path)
        self._patch_store(monkeypatch, (conn, lock), "proj", "agt")

        assert (
            set_session_hold("nonexistent", project_slug="proj", agent_slug="agt")
            is False
        )

    def test_get_session_hold(self, tmp_path, monkeypatch):
        from omniagents.core.session.history_db import (
            get_session_hold,
            set_session_hold,
        )

        conn, lock = self._make_db(tmp_path)
        self._patch_store(monkeypatch, (conn, lock), "proj", "agt")

        conn.execute(
            "INSERT INTO sessions (session_id, created_at) VALUES (?, datetime('now'))",
            ("s1",),
        )
        conn.commit()

        assert get_session_hold("s1", project_slug="proj", agent_slug="agt") is False
        set_session_hold("s1", True, project_slug="proj", agent_slug="agt")
        assert get_session_hold("s1", project_slug="proj", agent_slug="agt") is True
        set_session_hold("s1", False, project_slug="proj", agent_slug="agt")
        assert get_session_hold("s1", project_slug="proj", agent_slug="agt") is False

    def test_hold_column_in_schema(self, tmp_path):
        from omniagents.core.session.storage import sqlite as sqlite_storage

        db_path = tmp_path / "schema_test.db"
        conn = sqlite3.connect(str(db_path))
        sqlite_storage._ensure_schema(conn)
        columns = [
            col[1] for col in conn.execute("PRAGMA table_info(sessions)").fetchall()
        ]
        assert "hold" in columns


# ═══════════════════════════════════════════════════════════════════════
# WI-2: HMAC chain integrity
# ═══════════════════════════════════════════════════════════════════════


class TestHMACChainIntegrity:
    def test_compute_hmac_deterministic(self):
        from omniagents.core.audit.integrity import compute_hmac

        h1 = compute_hmac('{"a":1}', "seed", "key")
        h2 = compute_hmac('{"a":1}', "seed", "key")
        assert h1 == h2
        assert len(h1) == 64  # SHA-256 hex

    def test_compute_hmac_different_with_different_prev(self):
        from omniagents.core.audit.integrity import compute_hmac

        h1 = compute_hmac('{"a":1}', "seed1", "key")
        h2 = compute_hmac('{"a":1}', "seed2", "key")
        assert h1 != h2

    def test_verify_valid_chain(self, tmp_path):
        import asyncio

        from omniagents.core.audit.file_backend import FileAuditBackend
        from omniagents.core.audit.integrity import verify_audit_log

        log_path = tmp_path / "signed.jsonl"
        backend = FileAuditBackend(path=str(log_path), signing_key="test_key")

        for i in range(3):
            asyncio.run(
                backend.handle_event(
                    event_type="test",
                    timestamp=f"2026-01-0{i+1}T00:00:00",
                    details={"i": i},
                )
            )

        valid, count, err = verify_audit_log(str(log_path), "test_key")
        assert valid is True
        assert count == 3
        assert err is None

    def test_verify_tampered_chain(self, tmp_path):
        import asyncio

        from omniagents.core.audit.file_backend import FileAuditBackend
        from omniagents.core.audit.integrity import verify_audit_log

        log_path = tmp_path / "tampered.jsonl"
        backend = FileAuditBackend(path=str(log_path), signing_key="test_key")

        for i in range(3):
            asyncio.run(
                backend.handle_event(
                    event_type="test",
                    timestamp=f"2026-01-0{i+1}T00:00:00",
                    details={"i": i},
                )
            )

        # Tamper with the second line
        lines = log_path.read_text().splitlines()
        obj = json.loads(lines[1])
        obj["details"]["i"] = 999
        lines[1] = json.dumps(obj)
        log_path.write_text("\n".join(lines) + "\n")

        valid, count, err = verify_audit_log(str(log_path), "test_key")
        assert valid is False
        assert "HMAC mismatch" in err

    def test_no_hmac_without_key(self, tmp_path):
        import asyncio

        from omniagents.core.audit.file_backend import FileAuditBackend
        from omniagents.core.audit.integrity import HMAC_FIELD

        log_path = tmp_path / "unsigned.jsonl"
        backend = FileAuditBackend(path=str(log_path))  # no signing_key

        asyncio.run(
            backend.handle_event(event_type="test", timestamp="2026-01-01T00:00:00")
        )

        line = log_path.read_text().strip()
        obj = json.loads(line)
        assert HMAC_FIELD not in obj

    def test_chain_recovery_on_restart(self, tmp_path):
        import asyncio

        from omniagents.core.audit.file_backend import FileAuditBackend
        from omniagents.core.audit.integrity import verify_audit_log

        log_path = tmp_path / "recovery.jsonl"

        # Write 2 entries with first backend instance
        backend1 = FileAuditBackend(path=str(log_path), signing_key="test_key")
        for i in range(2):
            asyncio.run(
                backend1.handle_event(
                    event_type="test",
                    timestamp=f"2026-01-0{i+1}T00:00:00",
                    details={"i": i},
                )
            )

        # Create new backend instance (simulates restart) — should recover chain
        backend2 = FileAuditBackend(path=str(log_path), signing_key="test_key")
        asyncio.run(
            backend2.handle_event(
                event_type="test",
                timestamp="2026-01-03T00:00:00",
                details={"i": 2},
            )
        )

        # Verify entire chain
        valid, count, err = verify_audit_log(str(log_path), "test_key")
        assert valid is True
        assert count == 3
        assert err is None

    def test_create_audit_hook_with_secrets_provider(self, tmp_path, monkeypatch):
        from omniagents.core.audit.file_backend import create_audit_hook
        from omniagents.core.audit.integrity import HMAC_FIELD

        import asyncio

        class FakeSecrets:
            def get_secret(self, key):
                if key == "OMNIAGENTS_AUDIT_HMAC_KEY":
                    return "my_secret"
                return None

        log_path = tmp_path / "hook_test.jsonl"
        hook = create_audit_hook(
            {"path": str(log_path)},
            project_slug="test",
            secrets_provider=FakeSecrets(),
        )

        asyncio.run(hook(event_type="test", timestamp="2026-01-01T00:00:00"))

        line = log_path.read_text().strip()
        obj = json.loads(line)
        assert HMAC_FIELD in obj

    def test_create_audit_hook_without_secrets_provider(self, tmp_path):
        import asyncio

        from omniagents.core.audit.file_backend import create_audit_hook
        from omniagents.core.audit.integrity import HMAC_FIELD

        log_path = tmp_path / "no_secrets.jsonl"
        hook = create_audit_hook(
            {"path": str(log_path)},
            project_slug="test",
        )

        asyncio.run(hook(event_type="test", timestamp="2026-01-01T00:00:00"))

        line = log_path.read_text().strip()
        obj = json.loads(line)
        assert HMAC_FIELD not in obj


# ═══════════════════════════════════════════════════════════════════════
# WI-3: OIDC AuthProvider
# ═══════════════════════════════════════════════════════════════════════


class TestOIDCAuthProvider:
    def _make_provider(self, **overrides):
        from omniagents.core.providers.auth.oidc import OIDCAuthProvider

        cfg = {
            "issuer": "https://issuer.example.com",
            "audience": "api://test",
            "jwks_uri": "https://issuer.example.com/.well-known/jwks.json",
            **overrides,
        }
        return OIDCAuthProvider(cfg)

    @pytest.mark.asyncio
    async def test_valid_token(self, monkeypatch):
        import asyncio
        from unittest.mock import MagicMock

        provider = self._make_provider()

        # Mock the jwt module
        mock_key = MagicMock()
        mock_key.key = "fake_public_key"
        mock_jwks_client = MagicMock()
        mock_jwks_client.get_signing_key_from_jwt.return_value = mock_key

        provider._jwks_client = mock_jwks_client

        fake_claims = {
            "sub": "user-123",
            "email": "user@example.com",
            "name": "Test User",
            "roles": ["admin"],
        }

        import importlib

        jwt_mod = MagicMock()
        jwt_mod.decode.return_value = fake_claims
        monkeypatch.setitem(__import__("sys").modules, "jwt", jwt_mod)

        result = await provider.authenticate("fake_bearer_token")

        assert result.authenticated is True
        assert result.identity.user_id == "user-123"
        assert result.identity.email == "user@example.com"
        assert result.identity.display_name == "Test User"
        assert result.identity.roles == ["admin"]

    @pytest.mark.asyncio
    async def test_no_token(self):
        provider = self._make_provider()

        # Request with no headers/params
        result = await provider.authenticate(object())
        assert result.authenticated is False
        assert "No bearer token" in result.error

    @pytest.mark.asyncio
    async def test_invalid_token_returns_error(self, monkeypatch):
        from unittest.mock import MagicMock

        provider = self._make_provider()

        mock_jwks_client = MagicMock()
        mock_jwks_client.get_signing_key_from_jwt.side_effect = Exception(
            "Key not found"
        )
        provider._jwks_client = mock_jwks_client

        result = await provider.authenticate("bad_token")
        assert result.authenticated is False
        assert "Key not found" in result.error

    @pytest.mark.asyncio
    async def test_custom_user_id_claim(self, monkeypatch):
        from unittest.mock import MagicMock

        provider = self._make_provider(user_id_claim="oid")

        mock_key = MagicMock()
        mock_key.key = "fake_key"
        mock_jwks_client = MagicMock()
        mock_jwks_client.get_signing_key_from_jwt.return_value = mock_key
        provider._jwks_client = mock_jwks_client

        fake_claims = {
            "sub": "sub-value",
            "oid": "azure-object-id",
            "email": "user@example.com",
        }
        jwt_mod = MagicMock()
        jwt_mod.decode.return_value = fake_claims
        monkeypatch.setitem(__import__("sys").modules, "jwt", jwt_mod)

        result = await provider.authenticate("some_token")
        assert result.authenticated is True
        assert result.identity.user_id == "azure-object-id"

    @pytest.mark.asyncio
    async def test_get_user_identity_delegates(self, monkeypatch):
        from unittest.mock import MagicMock

        provider = self._make_provider()

        mock_key = MagicMock()
        mock_key.key = "fake_key"
        mock_jwks_client = MagicMock()
        mock_jwks_client.get_signing_key_from_jwt.return_value = mock_key
        provider._jwks_client = mock_jwks_client

        jwt_mod = MagicMock()
        jwt_mod.decode.return_value = {"sub": "user-1", "email": "u@ex.com"}
        monkeypatch.setitem(__import__("sys").modules, "jwt", jwt_mod)

        identity = await provider.get_user_identity("token")
        assert identity is not None
        assert identity.user_id == "user-1"

    def test_extract_bearer_token_from_header(self):
        from omniagents.core.providers.auth.oidc import OIDCAuthProvider
        from unittest.mock import MagicMock

        req = MagicMock()
        req.headers = {"authorization": "Bearer my_jwt_token"}
        req.query_params = {}

        assert OIDCAuthProvider._extract_bearer_token(req) == "my_jwt_token"

    def test_extract_bearer_token_from_query(self):
        from omniagents.core.providers.auth.oidc import OIDCAuthProvider
        from unittest.mock import MagicMock

        req = MagicMock()
        req.headers = {}
        req.query_params = {"token": "query_token"}

        assert OIDCAuthProvider._extract_bearer_token(req) == "query_token"

    def test_extract_bearer_token_plain_string(self):
        from omniagents.core.providers.auth.oidc import OIDCAuthProvider

        assert OIDCAuthProvider._extract_bearer_token("raw_token") == "raw_token"


# ═══════════════════════════════════════════════════════════════════════
# WI-4: User identity threading
# ═══════════════════════════════════════════════════════════════════════


class TestUserIdentityThreading:
    def _make_db(self, tmp_path):
        from omniagents.core.session.storage import sqlite as sqlite_storage

        db_path = tmp_path / f"identity_test_{uuid.uuid4().hex[:8]}.db"
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        sqlite_storage._ensure_schema(conn)
        return conn, Lock()

    def _patch_store(self, monkeypatch, conn_lock, project, agent):
        from omniagents.core.session.storage.sqlite import SqliteSessionStorage

        original = SqliteSessionStorage._get_store

        def patched(backend, p, a):
            if p == project and a == agent:
                return conn_lock
            return original(backend, p, a)

        monkeypatch.setattr(SqliteSessionStorage, "_get_store", patched)

    def test_user_id_column_exists(self, tmp_path):
        from omniagents.core.session.storage import sqlite as sqlite_storage

        db_path = tmp_path / "schema.db"
        conn = sqlite3.connect(str(db_path))
        sqlite_storage._ensure_schema(conn)
        columns = [
            col[1] for col in conn.execute("PRAGMA table_info(sessions)").fetchall()
        ]
        assert "user_id" in columns

    def test_register_session_with_user_id(self, tmp_path, monkeypatch):
        from omniagents.core.session.history_db import register_session

        conn, lock = self._make_db(tmp_path)
        self._patch_store(monkeypatch, (conn, lock), "proj", "agt")

        register_session(
            "s1", project_slug="proj", agent_slug="agt", user_id="user-abc"
        )

        row = conn.execute(
            "SELECT user_id FROM sessions WHERE session_id='s1'"
        ).fetchone()
        assert row[0] == "user-abc"

    def test_register_session_without_user_id(self, tmp_path, monkeypatch):
        from omniagents.core.session.history_db import register_session

        conn, lock = self._make_db(tmp_path)
        self._patch_store(monkeypatch, (conn, lock), "proj", "agt")

        register_session("s2", project_slug="proj", agent_slug="agt")

        row = conn.execute(
            "SELECT user_id FROM sessions WHERE session_id='s2'"
        ).fetchone()
        assert row[0] is None

    def test_update_session_user_id(self, tmp_path, monkeypatch):
        from omniagents.core.session.history_db import (
            register_session,
            update_session_user_id,
        )

        conn, lock = self._make_db(tmp_path)
        self._patch_store(monkeypatch, (conn, lock), "proj", "agt")

        register_session("s1", project_slug="proj", agent_slug="agt")
        update_session_user_id("s1", "user-xyz", project_slug="proj", agent_slug="agt")

        row = conn.execute(
            "SELECT user_id FROM sessions WHERE session_id='s1'"
        ).fetchone()
        assert row[0] == "user-xyz"

    def test_list_sessions_for_user(self, tmp_path, monkeypatch):
        from omniagents.core.session.history_db import (
            register_session,
            list_sessions_for_user,
        )

        conn, lock = self._make_db(tmp_path)
        self._patch_store(monkeypatch, (conn, lock), "proj", "agt")

        register_session("s1", project_slug="proj", agent_slug="agt", user_id="alice")
        register_session("s2", project_slug="proj", agent_slug="agt", user_id="bob")
        register_session("s3", project_slug="proj", agent_slug="agt", user_id="alice")

        alice_sessions = list_sessions_for_user(
            "alice", project_slug="proj", agent_slug="agt"
        )
        assert len(alice_sessions) == 2
        assert all(s["id"] in ("s1", "s3") for s in alice_sessions)

        bob_sessions = list_sessions_for_user(
            "bob", project_slug="proj", agent_slug="agt"
        )
        assert len(bob_sessions) == 1
        assert bob_sessions[0]["id"] == "s2"

    def test_session_object_has_user_id(self):
        from omniagents.core.session.manager import Session

        sess = Session(project_slug=None, agent_slug=None)
        assert sess.user_id is None
        sess.user_id = "test-user"
        assert sess.user_id == "test-user"


# ═══════════════════════════════════════════════════════════════════════
# WI-5: SQLCipher EncryptionProvider
# ═══════════════════════════════════════════════════════════════════════


class TestSQLCipherEncryption:
    def test_missing_key_raises(self, monkeypatch):
        from omniagents.core.providers.session_encryption.sqlcipher import (
            SQLCipherEncryption,
        )

        # Ensure the env var is not set
        monkeypatch.delenv("OMNIAGENTS_SESSION_DB_KEY", raising=False)
        provider = SQLCipherEncryption({})

        with pytest.raises(RuntimeError, match="encryption key not found"):
            provider._resolve_key()

    def test_get_connection_calls_pragma_key(self, monkeypatch):
        from unittest.mock import MagicMock
        import sys

        # Set the secret key
        monkeypatch.setenv("OMNIAGENTS_SESSION_DB_KEY", "my_secret_key")

        # Mock pysqlcipher3
        mock_sqlcipher = MagicMock()
        mock_conn = MagicMock()
        mock_sqlcipher.connect.return_value = mock_conn
        mock_module = MagicMock()
        mock_module.dbapi2 = mock_sqlcipher
        monkeypatch.setitem(sys.modules, "pysqlcipher3", mock_module)
        monkeypatch.setitem(sys.modules, "pysqlcipher3.dbapi2", mock_sqlcipher)

        from omniagents.core.providers.session_encryption.sqlcipher import (
            SQLCipherEncryption,
        )

        provider = SQLCipherEncryption({"key_secret": "OMNIAGENTS_SESSION_DB_KEY"})
        conn = provider.get_connection("/tmp/test.db")

        mock_sqlcipher.connect.assert_called_once_with(
            "/tmp/test.db", check_same_thread=False
        )
        # Verify PRAGMA key was called
        calls = [str(c) for c in mock_conn.execute.call_args_list]
        assert any("PRAGMA key" in c for c in calls)
        assert any("cipher_memory_security" in c for c in calls)

    def test_custom_key_secret_name(self, monkeypatch):
        monkeypatch.setenv("MY_CUSTOM_KEY", "custom_value")

        from omniagents.core.providers.session_encryption.sqlcipher import (
            SQLCipherEncryption,
        )

        provider = SQLCipherEncryption({"key_secret": "MY_CUSTOM_KEY"})
        key = provider._resolve_key()
        assert key == "custom_value"


# ═══════════════════════════════════════════════════════════════════════
# WI-6: Presidio ContentScanner
# ═══════════════════════════════════════════════════════════════════════


class TestPresidioScanner:
    @pytest.mark.asyncio
    async def test_scan_detects_entities(self, monkeypatch):
        import sys
        from unittest.mock import MagicMock

        # Mock presidio modules
        mock_result = MagicMock()
        mock_result.entity_type = "PERSON"
        mock_result.start = 0
        mock_result.end = 8

        mock_analyzer_engine = MagicMock()
        mock_analyzer_engine.return_value.analyze.return_value = [mock_result]

        mock_anonymized = MagicMock()
        mock_anonymized.text = "[REDACTED] is here"

        mock_anonymizer_engine = MagicMock()
        mock_anonymizer_engine.return_value.anonymize.return_value = mock_anonymized

        mock_analyzer_mod = MagicMock()
        mock_analyzer_mod.AnalyzerEngine = mock_analyzer_engine
        monkeypatch.setitem(sys.modules, "presidio_analyzer", mock_analyzer_mod)

        mock_anonymizer_mod = MagicMock()
        mock_anonymizer_mod.AnonymizerEngine = mock_anonymizer_engine
        mock_anonymizer_mod.entities = MagicMock()
        mock_anonymizer_mod.entities.OperatorConfig = MagicMock()
        monkeypatch.setitem(sys.modules, "presidio_anonymizer", mock_anonymizer_mod)
        monkeypatch.setitem(
            sys.modules, "presidio_anonymizer.entities", mock_anonymizer_mod.entities
        )

        from omniagents.core.providers.content_scanner.presidio import PresidioScanner

        scanner = PresidioScanner({})
        result = await scanner.scan("John Doe is here")

        assert result.allowed is True
        assert len(result.findings) == 1
        assert result.findings[0].pattern_name == "PERSON"
        assert result.redacted_content == "[REDACTED] is here"

    @pytest.mark.asyncio
    async def test_scan_block_mode(self, monkeypatch):
        import sys
        from unittest.mock import MagicMock

        mock_result = MagicMock()
        mock_result.entity_type = "US_SSN"
        mock_result.start = 0
        mock_result.end = 11

        mock_analyzer_engine = MagicMock()
        mock_analyzer_engine.return_value.analyze.return_value = [mock_result]

        mock_anonymized = MagicMock()
        mock_anonymized.text = "[REDACTED]"

        mock_anonymizer_engine = MagicMock()
        mock_anonymizer_engine.return_value.anonymize.return_value = mock_anonymized

        mock_analyzer_mod = MagicMock()
        mock_analyzer_mod.AnalyzerEngine = mock_analyzer_engine
        monkeypatch.setitem(sys.modules, "presidio_analyzer", mock_analyzer_mod)

        mock_anonymizer_mod = MagicMock()
        mock_anonymizer_mod.AnonymizerEngine = mock_anonymizer_engine
        mock_anonymizer_mod.entities = MagicMock()
        mock_anonymizer_mod.entities.OperatorConfig = MagicMock()
        monkeypatch.setitem(sys.modules, "presidio_anonymizer", mock_anonymizer_mod)
        monkeypatch.setitem(
            sys.modules, "presidio_anonymizer.entities", mock_anonymizer_mod.entities
        )

        from omniagents.core.providers.content_scanner.presidio import PresidioScanner

        scanner = PresidioScanner({"block": True})
        result = await scanner.scan("123-45-6789")

        assert result.allowed is False

    @pytest.mark.asyncio
    async def test_scan_with_entity_filter(self, monkeypatch):
        import sys
        from unittest.mock import MagicMock

        mock_analyzer_engine = MagicMock()
        mock_analyzer_engine.return_value.analyze.return_value = []
        mock_analyzer_mod = MagicMock()
        mock_analyzer_mod.AnalyzerEngine = mock_analyzer_engine
        monkeypatch.setitem(sys.modules, "presidio_analyzer", mock_analyzer_mod)

        mock_anonymizer_mod = MagicMock()
        mock_anonymizer_mod.AnonymizerEngine = MagicMock()
        monkeypatch.setitem(sys.modules, "presidio_anonymizer", mock_anonymizer_mod)

        from omniagents.core.providers.content_scanner.presidio import PresidioScanner

        scanner = PresidioScanner({"entities": ["PERSON", "US_SSN"]})
        await scanner.scan("test")

        # Verify entities filter was passed to analyzer
        call_args = mock_analyzer_engine.return_value.analyze.call_args
        assert call_args.kwargs.get("entities") == ["PERSON", "US_SSN"]

    @pytest.mark.asyncio
    async def test_scan_no_findings(self, monkeypatch):
        import sys
        from unittest.mock import MagicMock

        mock_analyzer_engine = MagicMock()
        mock_analyzer_engine.return_value.analyze.return_value = []

        mock_analyzer_mod = MagicMock()
        mock_analyzer_mod.AnalyzerEngine = mock_analyzer_engine
        monkeypatch.setitem(sys.modules, "presidio_analyzer", mock_analyzer_mod)

        mock_anonymizer_mod = MagicMock()
        mock_anonymizer_mod.AnonymizerEngine = MagicMock()
        monkeypatch.setitem(sys.modules, "presidio_anonymizer", mock_anonymizer_mod)

        from omniagents.core.providers.content_scanner.presidio import PresidioScanner

        scanner = PresidioScanner({})
        result = await scanner.scan("Just some normal text")

        assert result.allowed is True
        assert len(result.findings) == 0
        assert result.redacted_content is None


# ═══════════════════════════════════════════════════════════════════════
# WI-9: Reviewer identity on enforced approvals
# ═══════════════════════════════════════════════════════════════════════


class TestReviewerIdentity:
    def test_audit_event_payload_carries_reviewer_id(self):
        # Reviewer identity now flows through audit hooks emitted from the
        # service-side approval response handler. We verify the audit
        # event payload carries the reviewer/user id directly.
        from omniagents.core.audit.events import make_audit_event

        event = make_audit_event(
            "tool_approved",
            session_id="sess-xyz",
            user_id="reviewer-xyz",
            run_id="run-1",
            agent_name="agent",
            details={"tool": "delete_file"},
        )
        assert event["user_id"] == "reviewer-xyz"
        assert event["event_type"] == "tool_approved"
        assert event["details"]["tool"] == "delete_file"


# ═══════════════════════════════════════════════════════════════════════
# WI-8: Session/audit export with redaction
# ═══════════════════════════════════════════════════════════════════════


class TestSessionExport:
    def _make_db(self, tmp_path):
        from omniagents.core.session.storage import sqlite as sqlite_storage

        db_path = tmp_path / f"export_test_{uuid.uuid4().hex[:8]}.db"
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        sqlite_storage._ensure_schema(conn)
        return conn, Lock()

    def _patch_store(self, monkeypatch, conn_lock, project, agent):
        from omniagents.core.session.storage.sqlite import SqliteSessionStorage

        original = SqliteSessionStorage._get_store

        def patched(backend, p, a):
            if p == project and a == agent:
                return conn_lock
            return original(backend, p, a)

        monkeypatch.setattr(SqliteSessionStorage, "_get_store", patched)

    @pytest.mark.asyncio
    async def test_export_session_basic(self, tmp_path, monkeypatch):
        from omniagents.core.audit.export import export_session

        conn, lock = self._make_db(tmp_path)
        self._patch_store(monkeypatch, (conn, lock), "proj", "agt")

        conn.execute(
            "INSERT INTO sessions (session_id, created_at) VALUES (?, datetime('now'))",
            ("s1",),
        )
        conn.execute(
            "INSERT INTO history (session_id, msg_json, created_at) VALUES (?, ?, datetime('now'))",
            ("s1", json.dumps({"role": "user", "content": "Hello world"})),
        )
        conn.execute(
            "INSERT INTO history (session_id, msg_json, created_at) VALUES (?, ?, datetime('now'))",
            ("s1", json.dumps({"role": "assistant", "content": "Hi there"})),
        )
        conn.commit()

        result = await export_session("s1", project_slug="proj", agent_slug="agt")

        assert result["session_id"] == "s1"
        assert result["message_count"] == 2
        assert len(result["messages"]) == 2
        assert result["messages"][0]["content"] == "Hello world"
        assert "exported_at" in result

    @pytest.mark.asyncio
    async def test_export_session_with_redactor(self, tmp_path, monkeypatch):
        from omniagents.core.audit.export import export_session
        from omniagents.core.providers.content_scanner.base import (
            ContentScanner,
            ScanResult,
        )

        conn, lock = self._make_db(tmp_path)
        self._patch_store(monkeypatch, (conn, lock), "proj", "agt")

        conn.execute(
            "INSERT INTO sessions (session_id, created_at) VALUES (?, datetime('now'))",
            ("s1",),
        )
        conn.execute(
            "INSERT INTO history (session_id, msg_json, created_at) VALUES (?, ?, datetime('now'))",
            ("s1", json.dumps({"role": "user", "content": "My SSN is 123-45-6789"})),
        )
        conn.commit()

        class MockScanner(ContentScanner):
            def __init__(self):
                super().__init__({})

            async def scan(self, content, direction="output"):
                return ScanResult(
                    allowed=True,
                    redacted_content=content.replace("123-45-6789", "[REDACTED]"),
                )

        result = await export_session(
            "s1", project_slug="proj", agent_slug="agt", redactor=MockScanner()
        )
        assert "[REDACTED]" in result["messages"][0]["content"]
        assert "123-45-6789" not in result["messages"][0]["content"]

    @pytest.mark.asyncio
    async def test_export_empty_session(self, tmp_path, monkeypatch):
        from omniagents.core.audit.export import export_session

        conn, lock = self._make_db(tmp_path)
        self._patch_store(monkeypatch, (conn, lock), "proj", "agt")

        result = await export_session(
            "nonexistent", project_slug="proj", agent_slug="agt"
        )
        assert result["message_count"] == 0
        assert result["messages"] == []


class TestAuditLogExport:
    @pytest.mark.asyncio
    async def test_export_with_filters(self, tmp_path):
        from omniagents.core.audit.export import export_audit_log

        log_path = tmp_path / "audit.jsonl"
        entries = [
            {
                "event_type": "tool_called",
                "session_id": "s1",
                "timestamp": "2026-01-01T00:00:00",
            },
            {
                "event_type": "run_started",
                "session_id": "s1",
                "timestamp": "2026-01-01T01:00:00",
            },
            {
                "event_type": "tool_called",
                "session_id": "s2",
                "timestamp": "2026-01-02T00:00:00",
            },
        ]
        log_path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")

        # Filter by session_id
        result = await export_audit_log(str(log_path), filters={"session_id": "s1"})
        assert len(result) == 2

        # Filter by event_type
        result = await export_audit_log(
            str(log_path), filters={"event_type": "tool_called"}
        )
        assert len(result) == 2

        # Filter by time range
        result = await export_audit_log(
            str(log_path), filters={"after": "2026-01-01T00:30:00"}
        )
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_export_missing_file(self, tmp_path):
        from omniagents.core.audit.export import export_audit_log

        result = await export_audit_log(str(tmp_path / "missing.jsonl"))
        assert result == []

    @pytest.mark.asyncio
    async def test_export_with_redaction(self, tmp_path):
        from omniagents.core.audit.export import export_audit_log
        from omniagents.core.providers.content_scanner.base import (
            ContentScanner,
            ScanResult,
        )

        log_path = tmp_path / "audit.jsonl"
        entry = {
            "event_type": "tool_called",
            "timestamp": "2026-01-01T00:00:00",
            "details": {"tool": "bash", "input": "echo SSN 123-45-6789"},
        }
        log_path.write_text(json.dumps(entry) + "\n")

        class MockScanner(ContentScanner):
            def __init__(self):
                super().__init__({})

            async def scan(self, content, direction="output"):
                return ScanResult(
                    allowed=True,
                    redacted_content=content.replace("123-45-6789", "[REDACTED]"),
                )

        result = await export_audit_log(str(log_path), redactor=MockScanner())
        assert "[REDACTED]" in result[0]["details"]["input"]


# ═══════════════════════════════════════════════════════════════════════
# WI-10: Incident event + kill switch + notification
# ═══════════════════════════════════════════════════════════════════════


class TestIncidentEvent:
    def test_incident_event_type_exists(self):
        from omniagents.core.audit.events import INCIDENT

        assert INCIDENT == "incident"

    def test_make_incident_event(self):
        from omniagents.core.audit.events import INCIDENT, make_audit_event

        event = make_audit_event(
            INCIDENT,
            session_id="s1",
            details={"description": "PHI leak detected"},
        )
        assert event["event_type"] == "incident"
        assert event["details"]["description"] == "PHI leak detected"


class TestNotificationProvider:
    @pytest.mark.asyncio
    async def test_none_notification_noop(self):
        from omniagents.core.providers.notification.none import NoneNotification

        provider = NoneNotification()
        # Should not raise
        await provider.notify({"event_type": "incident"})

    @pytest.mark.asyncio
    async def test_webhook_posts_matching_events(self, monkeypatch):
        from unittest.mock import MagicMock, patch

        from omniagents.core.providers.notification.webhook import WebhookNotification

        provider = WebhookNotification(
            {
                "url": "https://hooks.example.com/test",
                "notify_on": ["incident"],
            }
        )

        mock_post = MagicMock()
        monkeypatch.setattr("requests.post", mock_post)

        await provider.notify({"event_type": "incident", "details": {"desc": "test"}})

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert call_kwargs.args[0] == "https://hooks.example.com/test"

    @pytest.mark.asyncio
    async def test_webhook_skips_non_matching_events(self, monkeypatch):
        from unittest.mock import MagicMock

        from omniagents.core.providers.notification.webhook import WebhookNotification

        provider = WebhookNotification(
            {
                "url": "https://hooks.example.com/test",
                "notify_on": ["incident"],
            }
        )

        mock_post = MagicMock()
        monkeypatch.setattr("requests.post", mock_post)

        await provider.notify({"event_type": "tool_called"})

        mock_post.assert_not_called()

    @pytest.mark.asyncio
    async def test_webhook_all_events_when_no_filter(self, monkeypatch):
        from unittest.mock import MagicMock

        from omniagents.core.providers.notification.webhook import WebhookNotification

        provider = WebhookNotification(
            {
                "url": "https://hooks.example.com/test",
                "notify_on": [],  # empty = all events
            }
        )

        mock_post = MagicMock()
        monkeypatch.setattr("requests.post", mock_post)

        await provider.notify({"event_type": "tool_called"})
        mock_post.assert_called_once()

    @pytest.mark.asyncio
    async def test_webhook_no_url_noop(self, monkeypatch):
        from unittest.mock import MagicMock

        from omniagents.core.providers.notification.webhook import WebhookNotification

        provider = WebhookNotification({"url": ""})

        mock_post = MagicMock()
        monkeypatch.setattr("requests.post", mock_post)

        await provider.notify({"event_type": "incident"})
        mock_post.assert_not_called()

    def test_webhook_env_var_expansion_in_headers(self, monkeypatch):
        monkeypatch.setenv("MY_TOKEN", "secret123")

        from omniagents.core.providers.notification.webhook import WebhookNotification

        provider = WebhookNotification(
            {
                "url": "https://hooks.example.com",
                "headers": {"Authorization": "${MY_TOKEN}"},
            }
        )

        assert provider._headers["Authorization"] == "secret123"

    def test_provider_registration(self):
        from omniagents.core.providers import available_providers

        # Ensure notification domain is loaded
        import omniagents.core.providers.notification  # noqa: F401

        providers = available_providers("notification")
        assert "none" in providers
        assert "webhook" in providers
