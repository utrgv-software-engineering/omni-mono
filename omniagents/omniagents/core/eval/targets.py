"""What an evaluation runs against: the agent target.

The engine (:mod:`omniagents.core.eval.engine`) drives every scenario the
same way - environment setup, turns, judges, measures, the run record - and
asks a target for only the agent-specific parts: build an agent for one
scenario run, send it one user turn, read back its history.

- :class:`ProjectAgentTarget` builds a project agent from its ``agent.yml``
  with the project's tools, server functions and capabilities, exactly as a
  real run would (``omniagents eval suite run``, Studio).
- :class:`SdkAgentTarget` wraps an ``agents.Agent`` built in code (the
  notebook API's ``EvalSuite.run(agent)``) and drives it with the SDK
  ``Runner``, keeping the conversation in memory.
"""

from __future__ import annotations

import inspect
import logging
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional, cast

import yaml

from omniagents.core.agents.factory import create_unified_agent
from omniagents.core.config.loader import load_agent_spec_from_yaml
from omniagents.core.session.history_db import load_history

from .environment import EnvironmentContext, EvalEnvironment
from .manifest import describe_agent_config, describe_sdk_agent
from .telemetry import consume_stream_events

__all__ = ["AgentTarget", "AgentRun", "ProjectAgentTarget", "SdkAgentTarget"]

_logger = logging.getLogger(__name__)


def _build_registries_for_eval(
    compiled_yaml_path: Path,
    project_root: Path | None = None,
) -> tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    """Build tool, server function, and capability registries for evaluation.

    Discovers tools from builtins, the project-level directories, and the
    agent's own subdirectories so that ``load_agent_spec_from_yaml`` can
    resolve every tool name and server function.

    The capability registry is sourced from the production
    ``load_project`` loader so the eval-side agent gets the *same* capability
    surface as a real run — including ``shell`` / ``filesystem`` / ``workers``,
    which carry the agent's ``execute_bash`` and file tools. Without it the
    loader skips every ``capabilities:`` entry ("no capability_registry
    available") and the agent runs toolless.

    Returns:
        Tuple of (tool_registry, server_functions_registry, capability_registry).
    """
    from omniagents.core.tools.discovery import discover_functions_in_dir
    from omniagents.core.server_functions.discovery import (
        discover_server_functions_in_dir,
    )

    tool_registry: Dict[str, Any] = {}
    sf_registry: Dict[str, Any] = {}

    # 1. Builtin tools (shipped with omniagents)
    core_root = Path(__file__).resolve().parents[2]
    builtin_dir = (core_root / "builtin" / "tools").resolve()
    if builtin_dir.is_dir():
        try:
            tool_registry.update(discover_functions_in_dir(str(builtin_dir)))
        except Exception:
            pass

    # 2. Resolve project root
    cfg_path = Path(compiled_yaml_path).resolve()
    if project_root is None:
        project_root = cfg_path.parent
        # Legacy fallback: compiled YAML in target/ subdir
        if project_root.name == "target":
            project_root = project_root.parent

    # Read project.yml to find configured tool and server_functions directories
    for candidate in ("project.yml", "project.yaml"):
        project_yml = project_root / candidate
        if project_yml.is_file():
            try:
                proj_cfg = yaml.safe_load(project_yml.read_text(encoding="utf-8")) or {}
                paths = proj_cfg.get("paths", {})
                tools_rel = paths.get("tools")
                if tools_rel:
                    tools_dir = (project_root / tools_rel).resolve()
                    if tools_dir.is_dir():
                        tool_registry.update(discover_functions_in_dir(str(tools_dir)))
                sf_rel = paths.get("server_functions")
                if sf_rel:
                    sf_dir = (project_root / sf_rel).resolve()
                    if sf_dir.is_dir():
                        sf_registry.update(
                            discover_server_functions_in_dir(str(sf_dir))
                        )
            except Exception:
                pass
            break

    # 3. Agent-level tools dir
    agent_tools_dir = cfg_path.parent / "tools"
    if agent_tools_dir.is_dir():
        try:
            tool_registry.update(discover_functions_in_dir(str(agent_tools_dir)))
        except Exception:
            pass

    # 4. Agent-level server_functions dir
    agent_sf_dir = cfg_path.parent / "server_functions"
    if agent_sf_dir.is_dir():
        try:
            sf_registry.update(discover_server_functions_in_dir(str(agent_sf_dir)))
        except Exception:
            pass

    # 5. Capability registry — reuse the production project loader so the
    # eval agent sees the same capabilities (shell/filesystem/workers/...) as
    # a real run. These carry execute_bash and the file tools; omitting them
    # leaves the agent unable to act. Best-effort, matching the rest of this
    # builder: an unloadable project yields an empty capability registry.
    capability_registry: Dict[str, Any] = {}
    try:
        from omniagents.core.project.runtime import load_project

        for candidate in ("project.yml", "project.yaml"):
            project_yml = project_root / candidate
            if project_yml.is_file():
                ctx = load_project(str(project_yml))
                capability_registry = dict(
                    getattr(ctx, "capability_registry", {}) or {}
                )
                break
    except Exception:
        capability_registry = {}

    return tool_registry, sf_registry, capability_registry


def _load_user_mcp_for_eval(
    compiled_yaml_path: Path,
    project_root: Path | None = None,
) -> List[Any]:
    """Resolve user MCP servers for an eval run.

    Mirrors what ``omniagents.core.project.runtime.load_project`` does for
    production runs: when ``project.yml`` declares ``allow_user_mcp_servers:
    true``, load mcp.json from the user-global config dir and the
    project-local one. The eval pipeline calls ``load_agent_spec_from_yaml``
    directly and would otherwise skip this entirely.

    Returns an empty list when the project doesn't opt in or when no
    mcp.json files are found.
    """
    cfg_path = Path(compiled_yaml_path).resolve()
    if project_root is None:
        project_root = cfg_path.parent
        if project_root.name == "target":
            project_root = project_root.parent

    proj_cfg: Dict[str, Any] = {}
    for candidate in ("project.yml", "project.yaml"):
        project_yml = project_root / candidate
        if project_yml.is_file():
            try:
                proj_cfg = yaml.safe_load(project_yml.read_text(encoding="utf-8")) or {}
            except Exception:
                proj_cfg = {}
            break

    if not proj_cfg.get("allow_user_mcp_servers"):
        return []

    try:
        from omniagents.core.config.user_mcp import load_user_mcp_config

        project_name = proj_cfg.get("name") or project_root.name
        return list(
            load_user_mcp_config(
                project_root=project_root,
                project_name=str(project_name),
            )
        )
    except Exception as exc:
        _logger.warning("user MCP config load failed: %s", exc)
        return []


def _apply_scenario_variables(spec, scenario: Dict[str, Any]) -> None:
    """Merge ``scenario.variables`` into ``spec.variables``; scenario wins.

    Mirrors the launcher's ``start_run(variables=...)`` pattern: scenario
    YAML can set per-scenario session variables (e.g.
    ``additional_instructions``) that flow through ``AgentSpec.variables``
    → ``RemoteAgent._variables`` → ``start_run`` → ``session.variables``,
    where the agent's ``build_context`` factory picks them up and renders
    them into the instructions template.

    Without this, scenarios cannot inject system-level guidance the way
    real callers (launcher autopilot, custom dispatchers) do — only the
    agent YAML's baseline ``variables:`` block flows through, and there
    is no per-scenario override.

    No-op if the scenario has no ``variables`` field or it is not a dict.
    """
    scenario_vars = scenario.get("variables")
    if not isinstance(scenario_vars, dict) or not scenario_vars:
        return
    if spec.variables is None:
        spec.variables = dict(scenario_vars)
    else:
        spec.variables = {**spec.variables, **scenario_vars}


class AgentRun(ABC):
    """One live agent for one scenario run."""

    #: Tool objects the agent was given (for ``tool_hallucination``).
    available_tools: List[Any] = []
    #: How a simulated user refers to the agent.
    description: str = "an AI assistant"

    @property
    def session_id(self) -> Optional[str]:
        return None

    @abstractmethod
    async def run_turn(self, prompt: str, run_config: Any = None) -> Dict[str, Any]:
        """Send one user message and run the agent to its answer.

        Returns the stream telemetry (``{"usage": {...}}``).
        """

    @abstractmethod
    def history(self) -> List[Dict[str, Any]]:
        """The conversation so far, as Responses input items."""

    def run_error(self) -> Optional[Dict[str, Any]]:
        """An error the agent runtime reported without raising, if any."""
        return None

    def annotate(self, metadata: Dict[str, Any]) -> None:
        """Record *metadata* on the run's conversation. A run with no
        conversation store (a bare SDK agent) has nothing to annotate."""
        return None

    async def cleanup(self) -> None:
        return None


class AgentTarget(ABC):
    """Builds the agent a scenario runs against."""

    #: Where scenario transcripts are exported (``None``: not exported).
    transcript_root: Optional[Path] = None
    #: Base directory for a judge's relative ``instructions_file``/``template_file``
    #: when the evaluation config has no directory of its own.
    base_dir: Path = Path.cwd()

    @abstractmethod
    async def start(
        self,
        scenario: Dict[str, Any],
        *,
        defaults: Dict[str, Any],
        environment: EvalEnvironment,
        context: Optional[EnvironmentContext],
    ) -> AgentRun:
        """Build a fresh agent for one run of ``scenario``."""

    @abstractmethod
    def describe(self) -> Dict[str, Any]:
        """The run manifest's agent fields (model, tools, hashes)."""

    @property
    def agent_yaml(self) -> Optional[Path]:
        return None


class _ProjectAgentRun(AgentRun):
    def __init__(self, agent: Any, spec: Any) -> None:
        self._agent = agent
        self._spec = spec
        self.available_tools = list(spec.available_tools or [])
        self.description = (
            getattr(spec, "description", None)
            or getattr(spec, "name", None)
            or "an AI assistant"
        )
        self._error: Optional[Dict[str, Any]] = None

    @property
    def session_id(self) -> Optional[str]:
        return getattr(self._agent, "_session_id", None)

    async def run_turn(self, prompt: str, run_config: Any = None) -> Dict[str, Any]:
        msgs = [{"role": "user", "content": prompt}]
        if run_config is not None:
            result = self._agent.run_streamed(msgs, run_config=run_config)
        else:
            result = self._agent.run_streamed(msgs)
        telemetry = await consume_stream_events(result.stream_events())
        get_last_run_end = getattr(self._agent, "get_last_run_end", None)
        if self._error is None and callable(get_last_run_end):
            try:
                candidate = get_last_run_end()
            except Exception:
                candidate = None
            if isinstance(candidate, dict) and candidate.get("error"):
                self._error = candidate
        return telemetry

    def history(self) -> List[Dict[str, Any]]:
        sid = self.session_id
        if not sid:
            return []
        return load_history(
            sid,
            project_slug=self._spec.project_slug,
            agent_slug=self._spec.agent_slug,
        )

    def run_error(self) -> Optional[Dict[str, Any]]:
        return self._error

    def annotate(self, metadata: Dict[str, Any]) -> None:
        """Merge *metadata* into the run's conversation, in the store
        ``history`` reads (thread metadata merges key by key)."""
        sid = self.session_id
        if not sid:
            return
        from omniagents.core.conversation.service import ConversationService

        ConversationService(
            project_slug=self._spec.project_slug,
            agent_slug=self._spec.agent_slug,
        ).update_thread(sid, can_access=lambda _owner: True, metadata=metadata)

    async def cleanup(self) -> None:
        cleanup = getattr(self._agent, "cleanup", None)
        if callable(cleanup):
            result = cleanup()
            if inspect.isawaitable(result):
                await result


class ProjectAgentTarget(AgentTarget):
    """A project agent loaded from its ``agent.yml``."""

    def __init__(
        self,
        agent_yaml: Path | str,
        *,
        project_root: Optional[Path] = None,
        agent_config: Optional[Dict[str, Any]] = None,
    ) -> None:
        from .transcripts import resolve_project_root

        self._agent_yaml = Path(agent_yaml).resolve()
        self.project_root = Path(project_root) if project_root else None
        self.transcript_root = self.project_root or resolve_project_root(
            self._agent_yaml
        )
        self.base_dir = self._agent_yaml.parent
        self._agent_config = agent_config

    @property
    def agent_yaml(self) -> Optional[Path]:
        return self._agent_yaml

    def describe(self) -> Dict[str, Any]:
        cfg = self._agent_config
        if cfg is None:
            try:
                cfg = yaml.safe_load(self._agent_yaml.read_text(encoding="utf-8"))
            except Exception:
                cfg = {}
        return describe_agent_config(cfg or {}, self._agent_yaml)

    async def start(
        self,
        scenario: Dict[str, Any],
        *,
        defaults: Dict[str, Any],
        environment: EvalEnvironment,
        context: Optional[EnvironmentContext],
    ) -> AgentRun:
        # Spec loading reads process env (XDG_CONFIG_HOME for
        # ``allow_user_mcp_servers``), so the engine applies the
        # environment's env vars before calling this.
        tool_reg, sf_reg, cap_reg = _build_registries_for_eval(
            self._agent_yaml, project_root=self.project_root
        )
        # Honor ``allow_user_mcp_servers`` so evals see the same MCP surface
        # as production.
        user_mcp = _load_user_mcp_for_eval(self._agent_yaml, self.project_root)
        spec = load_agent_spec_from_yaml(
            str(self._agent_yaml),
            tool_registry=tool_reg or None,
            server_functions_registry=sf_reg or None,
            capability_registry=cap_reg or None,
            user_mcp_servers=user_mcp or None,
        )
        spec.use_safe_agent = False
        # Per-scenario session variables (e.g. additional_instructions).
        _apply_scenario_variables(spec, scenario)
        if defaults.get("max_turns") is not None:
            spec.max_turns = int(defaults["max_turns"])
        # Anchor builtin filesystem tools to the eval invocation's cwd; without
        # it start_run raises WorkspaceRootRequiredError under strict mode.
        if not isinstance(spec.context, dict):
            spec.context = {} if spec.context is None else {"_original": spec.context}
        spec.context.setdefault("workspace_root", os.getcwd())
        if context and context.data:
            spec.context.update(context.data)
        # The environment may wire runtime-only objects (a live sandbox
        # session) into the still-mutable spec.
        await environment.prepare_spec(spec, context)
        agent = await create_unified_agent(spec)
        return _ProjectAgentRun(agent, spec)


class _SdkAgentRun(AgentRun):
    def __init__(self, agent: Any, max_turns: int, context: Any) -> None:
        self._agent = agent
        self._max_turns = max_turns
        self._context = context
        self._items: List[Dict[str, Any]] = []
        self.available_tools = list(getattr(agent, "tools", None) or [])
        self.description = getattr(agent, "name", None) or "an AI assistant"

    async def run_turn(self, prompt: str, run_config: Any = None) -> Dict[str, Any]:
        from agents import Runner

        kwargs: Dict[str, Any] = {"max_turns": self._max_turns}
        if run_config is not None:
            kwargs["run_config"] = run_config
        if self._context is not None:
            kwargs["context"] = self._context
        # Input items are plain Responses dicts; mypy can't prove them
        # against the SDK's TypedDict union.
        items = cast(Any, self._items + [{"role": "user", "content": prompt}])
        result = Runner.run_streamed(self._agent, items, **kwargs)
        telemetry = await consume_stream_events(result.stream_events())
        self._items = [dict(cast(Any, i)) for i in result.to_input_list()]
        return telemetry

    def history(self) -> List[Dict[str, Any]]:
        return list(self._items)


class SdkAgentTarget(AgentTarget):
    """An ``agents.Agent`` built in code, run with the SDK ``Runner``.

    The conversation is kept in memory between turns, nothing is persisted
    and no transcript is exported. ``max_turns`` is the agent's per-answer
    turn limit unless ``evaluation.defaults.max_turns`` sets one;
    ``context`` is passed to every run as the SDK run context.
    """

    def __init__(self, agent: Any, *, max_turns: int = 10, context: Any = None):
        self.agent = agent
        self.max_turns = int(max_turns)
        self.context = context
        self.transcript_root = None
        self.base_dir = Path.cwd()

    def describe(self) -> Dict[str, Any]:
        return describe_sdk_agent(self.agent, self.max_turns)

    async def start(
        self,
        scenario: Dict[str, Any],
        *,
        defaults: Dict[str, Any],
        environment: EvalEnvironment,
        context: Optional[EnvironmentContext],
    ) -> AgentRun:
        max_turns = defaults.get("max_turns")
        return _SdkAgentRun(
            self.agent,
            int(max_turns) if max_turns is not None else self.max_turns,
            self.context,
        )
