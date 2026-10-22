"""Declarative slash-command → project-agent bindings.

A product binds a slash command to a one-shot project agent run in its
manifest instead of registering a Python handler:

.. code-block:: yaml

    slash_commands:
      init:
        agent: init_agent          # agents/<key>/agent.yml (required)
        prompt: begin              # kickoff user message (default "begin")
        max_turns: 1000            # turn budget (default 50)
        normalize_model: false     # keep the runtime's raw model id (default true)
        failure_message: Failed to update AGENTS.md  # returned when the run raises
        attach_file:               # optional success-attachment protocol
          path: AGENTS.md          # workspace-relative file read back after the run
          when_status:             # exact final-line statuses that trigger attachment
            - Created AGENTS.md
            - Updated AGENTS.md
          language: markdown       # code-fence language (default markdown)

``AgentService`` registers each binding as a server function of the
same name carrying a slash declaration (see
``omniagents.core.server_functions.slash``), so clients list it in the
slash catalog and run it through ``server_call``; nothing goes through
the model. A Python-registered server function of the same name wins.

Runner semantics (:func:`run_slash_agent`), generalized from omni-code's
``/init`` handler:

- Model: ``resolve_model_runtime(normalize=<normalize_model>)`` — the
  configured default model; ``normalize_model: false`` keeps the
  runtime's raw model id (historical behavior for agent runs that need
  the provider-prefixed id). The runtime's ``model_settings`` are
  stamped on the spec when non-empty.
- Workspace: the invoking session's environment — the command is routed
  to it like any workspace operation, and the run binds its workspace. A
  standalone CLI run uses the invoking shell's directory (``$PWD``, falling
  back to the process cwd). ``attach_file.path`` is read back from the
  same anchor.
- Run: multi-turn, through
  :func:`omniagents.core.runtime.run_project_agent_once` (outside the
  session lifecycle).
- Result: the agent's final output line is the status. When the binding
  declares ``attach_file`` and the status is in ``when_status`` (or
  ``when_status`` is omitted) and the file exists, the response becomes
  ``"<status>\\n\\n<path>:\\n\\n```<language>\\n<body>\\n```"``. Any
  exception from the run returns ``failure_message`` (or a generic
  ``Failed to run /<name>.``) followed by the error. The final-line protocol itself — which
  exact strings the agent may end with — is the agent's instructions'
  business, not the runner's.

The argument text after the command name is ignored, matching the
handlers these bindings replace; bind arg-sensitive commands in Python.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_SLASH_AGENT_MAX_TURNS",
    "manifest_slash_bindings",
    "run_slash_agent",
]

DEFAULT_SLASH_AGENT_MAX_TURNS = 50


def _normalize(name: str) -> str:
    return (name or "").strip().lower().lstrip("/")


def manifest_slash_bindings(project_path: Any = None) -> Dict[str, dict]:
    """The ``slash_commands:`` bindings a project manifest declares.

    *project_path* is a project.yml path or a directory containing one;
    when omitted, the installed product identity's manifest is used
    (``product_info().manifest_path``). A light manifest read — only the
    ``slash_commands`` mapping is consulted. Returns ``{}`` when there
    is no manifest or no declaration; keys are normalized command names.
    """
    import yaml

    if project_path is None:
        from omniagents.core.product import product_info

        project_path = product_info().manifest_path
    if project_path is None:
        return {}
    manifest = Path(project_path).expanduser()
    if manifest.is_dir():
        manifest = manifest / "project.yml"
    if not manifest.is_file():
        return {}
    try:
        proj = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    raw = proj.get("slash_commands") if isinstance(proj, dict) else None
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, dict] = {}
    for key, value in raw.items():
        name = _normalize(str(key))
        if name and isinstance(value, dict):
            out[name] = value
    return out


async def run_slash_agent(
    name: str,
    binding: dict,
    arg: Optional[str] = None,
    *,
    service: Any = None,
    session: Any = None,
) -> str:
    """Run the project agent a slash binding names and format its result.

    See the module docstring for the binding schema and semantics. *arg*
    is accepted for registry-handler shape compatibility but ignored.
    """
    from omniagents.core.models import resolve_model_runtime
    from omniagents.core.runtime.ephemeral import run_project_agent_once

    agent_key = str(binding.get("agent") or "").strip()
    if not agent_key:
        return f"Slash command /{_normalize(name)} declares no agent."
    prompt = str(binding.get("prompt") or "begin")
    max_turns = int(binding.get("max_turns") or DEFAULT_SLASH_AGENT_MAX_TURNS)
    normalize = bool(binding.get("normalize_model", True))

    from omniagents.core.models import (
        resolve_service_model_runtime,
        normalize_model_name,
    )

    # Session-aware invocations use the same selection as chat. Standalone CLI
    # invocations intentionally use the workspace default.
    if session is not None:
        ref, model_runtime = resolve_service_model_runtime(service, session)
        model = (
            normalize_model_name(ref, model_runtime)
            if normalize
            else model_runtime.get("model")
        )
    else:
        model, model_runtime = resolve_model_runtime(normalize=normalize)

    # Anchor the run to the session's environment when the command was routed
    # to one; a standalone CLI run uses the invoking shell's directory ($PWD,
    # falling back to process cwd). The agent's file tools resolve relative
    # paths against it, and attach_file reads back from the same anchor.
    from omniagents.execution import current_request_context

    lease = getattr(current_request_context(), "environment_lease", None)
    if lease is not None:
        base_dir = Path(lease.workspace.root)
    else:
        workspace_root = os.environ.get("PWD")
        base_dir = Path(workspace_root).expanduser() if workspace_root else Path.cwd()

    # The agent's file and shell tools act on the current workspace, which a
    # run outside the session lifecycle must bind itself.
    from omniagents.workspace import reset_workspace, set_workspace

    workspace_token = set_workspace(lease.workspace) if lease is not None else None
    try:
        status = await run_project_agent_once(
            agent_key,
            prompt,
            model=model,
            runtime=model_runtime,
            session=session,
            # The harness stamps model_settings only when it's a
            # non-empty dict, so the runtime value passes through raw.
            model_settings=model_runtime.get("model_settings"),
            workspace_root=str(base_dir),
            max_turns=max_turns,
        )
    except Exception as exc:
        logger.exception("slash agent /%s failed", _normalize(name))
        failure = binding.get("failure_message")
        if not (isinstance(failure, str) and failure.strip()):
            failure = f"Failed to run /{_normalize(name)}."
        return f"{failure} ({exc})"
    finally:
        if workspace_token is not None:
            reset_workspace(workspace_token)

    attach = binding.get("attach_file")
    if isinstance(attach, dict):
        rel = str(attach.get("path") or "").strip()
        when = attach.get("when_status")
        statuses = {str(s) for s in when} if isinstance(when, list) and when else None
        language = str(attach.get("language") or "markdown")
        if rel and (statuses is None or status in statuses):
            file_path = base_dir / rel
            if file_path.exists():
                try:
                    body = file_path.read_text(encoding="utf-8")
                    return f"{status}\n\n{rel}:\n\n```{language}\n{body}\n```"
                except Exception:
                    return status
    return status
