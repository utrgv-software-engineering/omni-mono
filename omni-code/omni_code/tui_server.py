"""JSON-RPC server over stdin/stdout for the TUI.

The TUI node process is our child. We communicate over a dedicated pipe (fd 3
on the child side). The protocol is newline-delimited JSON-RPC 2.0.

Requests flow:  TUI -> pipe -> this server -> ProjectsRuntime -> response -> pipe -> TUI
"""

import asyncio
import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional

from omni_code.projects_runtime import ProjectsRuntime


class TuiRpcServer:
    def __init__(self, runtime: ProjectsRuntime):
        self._runtime = runtime
        self._writer: Optional[asyncio.StreamWriter] = None
        self._methods: dict[str, Any] = {
            "projects.list": self._projects_list,
            "projects.add": self._projects_add,
            "projects.remove": self._projects_remove,
            "pipeline.get": self._pipeline_get,
            "tickets.list": self._tickets_list,
            "tickets.add": self._tickets_add,
            "tickets.move": self._tickets_move,
            "tickets.delete": self._tickets_delete,
            "tickets.update": self._tickets_update,
            "sandbox.start": self._sandbox_start,
            "supervisor.start": self._supervisor_start,
            "supervisor.stop": self._supervisor_stop,
            "supervisor.auto_start": self._supervisor_auto_start,
            "supervisor.auto_stop": self._supervisor_auto_stop,
            "snapshot": self._snapshot,
            "ticket.history": self._ticket_history,
            "ticket.artifacts": self._ticket_artifacts,
            "ticket.artifact_read": self._ticket_artifact_read,
            "models.list": self._models_list,
            "models.add": self._models_add,
            "models.remove": self._models_remove,
            "models.set_default": self._models_set_default,
            "providers.list": self._providers_list,
            "providers.add": self._providers_add,
            "providers.remove": self._providers_remove,
            "network.get": self._network_get,
            "network.save": self._network_save,
            "env.get": self._env_get,
            "env.save": self._env_save,
            "containers.list": self._containers_list,
            "containers.start": self._containers_start,
            "containers.stop": self._containers_stop,
            "containers.rebuild": self._containers_rebuild,
            "sandbox.rebuild_image": self._sandbox_rebuild_image,
            "git.diff": self._git_diff,
            "git.diff_file": self._git_diff_file,
            "ticket.artifact_open": self._ticket_artifact_open,
            "projects.set_auto_dispatch": self._projects_set_auto_dispatch,
            "projects.get_auto_dispatch": self._projects_get_auto_dispatch,
            "projects.set_sandbox": self._projects_set_sandbox,
            "projects.get_sandbox": self._projects_get_sandbox,
            "mcp.list": self._mcp_list,
            "mcp.save": self._mcp_save,
            "models.test": self._models_test,
            "onboarding.status": self._onboarding_status,
            "terminal.resolve_ssh": self._terminal_resolve_ssh,
            "terminal.resolve_omni": self._terminal_resolve_omni,
            "terminal.resolve_chat": self._terminal_resolve_chat,
            "project.resolve_shell": self._project_resolve_shell,
            "project.resolve_omni": self._project_resolve_omni,
            "sessions.list": self._sessions_list,
            "projects.get_hotkeys": self._projects_get_hotkeys,
            "projects.set_hotkey": self._projects_set_hotkey,
        }

    def _send(self, obj: dict[str, Any]) -> None:
        if self._writer is None:
            return
        line = json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n"
        self._writer.write(line.encode("utf-8"))

    def _respond(self, req_id: Any, result: Any) -> None:
        self._send({"jsonrpc": "2.0", "id": req_id, "result": result})

    def _error(self, req_id: Any, code: int, message: str) -> None:
        self._send({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}})

    async def _handle_request(self, raw: dict[str, Any]) -> None:
        req_id = raw.get("id")
        method = raw.get("method", "")
        params = raw.get("params") or {}

        handler = self._methods.get(method)
        if handler is None:
            self._error(req_id, -32601, f"Method not found: {method}")
            return

        try:
            # Run handlers in a thread so they can safely call asyncio.run()
            # (e.g. supervisor_start/stop/send use asyncio.run for WebSocket RPC)
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, handler, params)
            self._respond(req_id, result)
        except Exception as exc:
            self._error(req_id, -32000, str(exc))

    async def serve(self, read_fd: int, write_fd: int) -> None:
        loop = asyncio.get_event_loop()
        reader = asyncio.StreamReader()
        read_transport, _ = await loop.connect_read_pipe(
            lambda: asyncio.StreamReaderProtocol(reader),
            os.fdopen(read_fd, "rb", 0),
        )
        write_pipe = os.fdopen(write_fd, "wb", 0)
        write_transport, _ = await loop.connect_write_pipe(
            asyncio.Protocol,
            write_pipe,
        )
        self._writer = asyncio.StreamWriter(write_transport, None, reader, loop)

        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    continue
                await self._handle_request(raw)
        except (asyncio.CancelledError, ConnectionError):
            pass
        finally:
            read_transport.close()
            write_transport.close()

    # --- RPC method handlers ---

    def _projects_list(self, params: dict) -> list[dict]:
        projects = self._runtime.list_projects()
        tasks = self._runtime.list_tasks()
        task_by_ticket: dict[str, dict] = {}
        for task in tasks:
            if task.ticket_id:
                task_by_ticket[task.ticket_id] = asdict(task)

        result = []
        for project in projects:
            tickets = self._runtime.list_tickets(project.id)
            active_count = 0
            for ticket in tickets:
                task = task_by_ticket.get(ticket.id)
                if task and task.get("status", {}).get("type") == "running":
                    active_count += 1
            result.append({
                **asdict(project),
                "ticket_count": len(tickets),
                "active_count": active_count,
            })
        return result

    def _projects_add(self, params: dict) -> dict:
        project = self._runtime.add_project(
            label=params["label"],
            workspace_dir=params["workspace_dir"],
            project_id=params.get("id"),
        )
        return asdict(project)

    def _projects_remove(self, params: dict) -> None:
        self._runtime.remove_project(params["project_id"])
        return None

    def _pipeline_get(self, params: dict) -> dict:
        pipeline = self._runtime.get_pipeline(params["project_id"])
        return {"columns": [asdict(col) for col in pipeline.columns]}

    def _tickets_list(self, params: dict) -> list[dict]:
        tickets = self._runtime.list_tickets(params.get("project_id"))
        tasks = self._runtime.list_tasks(params.get("project_id"))
        task_by_ticket: dict[str, dict] = {}
        for task in tasks:
            if task.ticket_id:
                task_by_ticket[task.ticket_id] = asdict(task)

        result = []
        for ticket in tickets:
            entry = asdict(ticket)
            task = task_by_ticket.get(ticket.id)
            entry["task"] = task
            result.append(entry)
        return result

    def _tickets_add(self, params: dict) -> dict:
        ticket = self._runtime.add_ticket(
            project_ref=params["project_id"],
            title=params["title"],
            description=params.get("description", ""),
            priority=params.get("priority", "medium"),
            blocked_by=params.get("blocked_by"),
        )
        return asdict(ticket)

    def _tickets_move(self, params: dict) -> dict:
        ticket = self._runtime.move_ticket(
            params["ticket_id"],
            params["column_id"],
            params.get("project_id"),
        )
        return asdict(ticket)

    def _tickets_delete(self, params: dict) -> None:
        ticket = self._runtime.resolve_ticket(params["ticket_id"], params.get("project_id"))
        if ticket is None:
            raise ValueError(f"Ticket not found: {params['ticket_id']}")
        config = self._runtime._load()
        config["fleetTickets"] = [
            raw for raw in config.get("fleetTickets", [])
            if raw.get("id") != ticket.id
        ]
        config["fleetTasks"] = [
            raw for raw in config.get("fleetTasks", [])
            if raw.get("ticketId") != ticket.id
        ]
        self._runtime._save(config)
        return None

    def _tickets_update(self, params: dict) -> dict:
        ticket = self._runtime.update_ticket_fields(
            params["ticket_id"],
            params["patch"],
            params.get("project_id"),
        )
        return asdict(ticket)

    def _sandbox_start(self, params: dict) -> dict:
        ticket_id = params["ticket_id"]
        project_id = params.get("project_id")
        sandbox = self._get_sandbox_for_ticket(ticket_id, project_id)

        if not sandbox:
            # No sandbox configured — running locally, nothing to start
            from omni_code.work_cli import _resolve_ticket_workspace
            workspace = _resolve_ticket_workspace(ticket_id, project_id)
            return {
                "container_name": None,
                "workspace": str(workspace),
                "ws_url": None,
                "sandbox_url": None,
                "code_server_url": None,
                "desktop_url": None,
            }

        from omni_code.work_cli import (
            _resolve_ticket_workspace,
            _ensure_started,
            _inspect_container,
            _work_urls,
            _record_task_state,
        )

        workspace = _resolve_ticket_workspace(ticket_id, project_id)
        container_name = _ensure_started(workspace, rebuild=False)
        details = _inspect_container(container_name)
        _record_task_state(workspace, details, ticket_ref=ticket_id)
        urls = _work_urls(details)
        return {
            "container_name": container_name,
            "workspace": str(workspace),
            "ws_url": urls.get("ws_url"),
            "sandbox_url": urls.get("sandbox_url"),
            "code_server_url": urls.get("code_server_url"),
            "desktop_url": urls.get("desktop_url"),
        }

    def _supervisor_start(self, params: dict) -> dict:
        ticket_id = params["ticket_id"]
        project_id = params.get("project_id")
        prompt = params.get("prompt", "Begin working on this ticket.")
        sandbox = self._get_sandbox_for_ticket(ticket_id, project_id)

        if not sandbox:
            raise ValueError(
                "Supervisor mode requires a sandbox. "
                "Configure a sandbox for this project, or use the Chat tab to interact with the agent locally."
            )

        from omni_code.work_cli import supervisor_start
        return supervisor_start(ticket_id, prompt, project_id)

    def _supervisor_stop(self, params: dict) -> None:
        ticket_id = params["ticket_id"]
        project_id = params.get("project_id")
        sandbox = self._get_sandbox_for_ticket(ticket_id, project_id)

        if not sandbox:
            raise ValueError("No sandbox configured — supervisor is not running.")

        from omni_code.work_cli import supervisor_stop
        supervisor_stop(ticket_id, project_id)
        return None

    def _supervisor_auto_start(self, params: dict) -> dict:
        ticket_id = params["ticket_id"]
        project_id = params.get("project_id")
        prompt = params.get("prompt", "Begin working on this ticket.")
        sandbox = self._get_sandbox_for_ticket(ticket_id, project_id)
        if not sandbox:
            raise ValueError("Supervisor auto mode requires a sandbox.")
        from omni_code.work_cli import supervisor_auto_start
        return supervisor_auto_start(ticket_id, prompt, project_id)

    def _supervisor_auto_stop(self, params: dict) -> dict:
        ticket_id = params["ticket_id"]
        project_id = params.get("project_id")
        from omni_code.work_cli import supervisor_auto_stop
        return supervisor_auto_stop(ticket_id, project_id)

    def _snapshot(self, params: dict) -> dict:
        return self._runtime.export_snapshot()

    def _ticket_history(self, params: dict) -> list:
        from omni_code.work_cli import _resolve_session_id, _format_history_messages
        from omniagents.core.session.history_db import load_history as oa_load_history

        ticket_id = params["ticket_id"]
        project_id = params.get("project_id")
        limit = params.get("limit", 50)

        session_id = _resolve_session_id(ticket_id, project_id)
        messages = _format_history_messages(
            oa_load_history(session_id, project_slug="omni_code", agent_slug="omni")
        )
        if limit:
            messages = messages[-limit:]
        return messages

    def _ticket_artifacts(self, params: dict) -> list:
        from omni_code.work_cli import _ticket_artifacts_dir

        ticket_id = params["ticket_id"]
        subdir = params.get("subdir")
        root = _ticket_artifacts_dir(ticket_id)
        target = root / subdir if subdir else root
        if not target.exists() or not target.is_dir():
            return []
        entries = []
        for path in sorted(target.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
            entries.append({
                "name": path.name,
                "path": str(path.relative_to(root)),
                "is_dir": path.is_dir(),
                "size": path.stat().st_size,
            })
        return entries

    def _ticket_artifact_read(self, params: dict) -> dict:
        import mimetypes
        from omni_code.work_cli import _ticket_artifacts_dir

        ticket_id = params["ticket_id"]
        file_path = params["path"]
        root = _ticket_artifacts_dir(ticket_id)
        target = (root / file_path).resolve()
        if not str(target).startswith(str(root.resolve())):
            raise ValueError("Path traversal detected")
        if not target.exists() or not target.is_file():
            raise ValueError(f"Artifact not found: {file_path}")
        mime_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        size = target.stat().st_size
        try:
            content = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return {"content": None, "mime_type": mime_type, "size": size}
        return {"content": content, "mime_type": mime_type, "size": size}

    def _models_list(self, params: dict) -> list:
        from omni_code.models import list_models
        return list_models()

    def _models_add(self, params: dict) -> None:
        from omni_code.models import add_model
        add_model(
            provider_name=params["provider_name"],
            provider_type=params["provider_type"],
            model_name=params["model_name"],
            model=params["model"],
            label=params.get("label"),
            provider_api_key=params.get("provider_api_key"),
            provider_base_url=params.get("provider_base_url"),
            provider_api_version=params.get("provider_api_version"),
            reasoning=params.get("reasoning"),
            set_default=params.get("set_default", False),
        )
        return None

    def _models_remove(self, params: dict) -> bool:
        from omni_code.models import remove_model
        return remove_model(params["name"])

    def _models_set_default(self, params: dict) -> bool:
        from omni_code.models import set_default_model
        return set_default_model(params["name"])

    def _providers_list(self, params: dict) -> list:
        from omni_code.models import list_providers
        return list_providers()

    def _providers_add(self, params: dict) -> None:
        from omni_code.models import add_provider
        add_provider(
            params["name"],
            params["type"],
            api_key=params.get("api_key"),
            base_url=params.get("base_url"),
            api_version=params.get("api_version"),
        )
        return None

    def _providers_remove(self, params: dict) -> bool:
        from omni_code.models import remove_provider
        return remove_provider(params["name"])

    def _network_get(self, params: dict) -> dict:
        from omni_code.proxy.config import load_user_network_config
        return load_user_network_config()

    def _network_save(self, params: dict) -> None:
        from omni_code.proxy.config import save_user_network_config
        save_user_network_config(params.get("config", {}))
        return None

    def _env_get(self, params: dict) -> dict:
        from omni_code.models import get_config_dir
        env_path = get_config_dir() / ".env"
        result: dict[str, str] = {}
        if env_path.is_file():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, _, value = line.partition("=")
                    result[key.strip()] = value.strip()
        return result

    def _env_save(self, params: dict) -> None:
        from omni_code.models import get_config_dir
        env_path = get_config_dir() / ".env"
        env_path.parent.mkdir(parents=True, exist_ok=True)
        entries = params.get("entries", {})
        lines = [f"{k}={v}" for k, v in entries.items()]
        env_path.write_text("\n".join(lines) + "\n" if lines else "", encoding="utf-8")
        return None

    def _containers_list(self, params: dict) -> list:
        import subprocess as sp
        try:
            result = sp.run(
                ["docker", "ps", "-a", "--filter", "name=omni", "--format",
                 '{"name":"{{.Names}}","status":"{{.Status}}","image":"{{.Image}}","ports":"{{.Ports}}","created":"{{.CreatedAt}}","state":"{{.State}}"}'],
                capture_output=True, text=True, timeout=10,
            )
            containers = []
            for line in result.stdout.strip().splitlines():
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    containers.append(entry)
                except json.JSONDecodeError:
                    continue
            return containers
        except Exception as exc:
            raise RuntimeError(f"Failed to list containers: {exc}")

    def _containers_start(self, params: dict) -> None:
        import subprocess as sp
        name = params["name"]
        sp.run(["docker", "start", name], capture_output=True, text=True, timeout=30, check=True)
        return None

    def _containers_stop(self, params: dict) -> None:
        import subprocess as sp
        name = params["name"]
        sp.run(["docker", "stop", name], capture_output=True, text=True, timeout=30, check=True)
        return None

    def _containers_rebuild(self, params: dict) -> dict:
        import subprocess as sp
        name = params["name"]
        # Stop if running
        sp.run(["docker", "stop", name], capture_output=True, text=True, timeout=30)
        # Remove container
        sp.run(["docker", "rm", name], capture_output=True, text=True, timeout=30)
        return {"removed": name, "note": "Container removed. Start a ticket sandbox to recreate."}

    def _sandbox_rebuild_image(self, params: dict) -> dict:
        """Rebuild the Docker image for a project's sandbox."""
        from omni_code.sandbox_cli import _sandbox_image_tag, _build_sandbox_image

        project_id = params["project_id"]
        project = self._runtime.resolve_project(project_id)
        if not project:
            raise ValueError(f"Project not found: {project_id}")
        sandbox = self._get_project_sandbox(project_id)
        if not sandbox:
            raise ValueError("No sandbox configured for this project")

        custom_dockerfile = sandbox.get("dockerfile")
        if custom_dockerfile:
            # Resolve relative to workspace
            dockerfile = str(Path(project.workspace_dir) / custom_dockerfile)
        else:
            from omni_code.work_cli import _work_dockerfile_path
            dockerfile = _work_dockerfile_path()

        custom_image = sandbox.get("image")
        if custom_image:
            return {"image": custom_image, "note": "Using a pre-built image — nothing to rebuild."}

        tag = _sandbox_image_tag(dockerfile)
        _build_sandbox_image(tag, dockerfile=dockerfile, no_cache=True)

        # Find running containers still on an older image
        import subprocess as sp
        stale: list[str] = []
        try:
            result = sp.run(
                ["docker", "ps", "-a", "--filter", "name=omni-work-",
                 "--format", "{{.Names}}\t{{.Image}}"],
                capture_output=True, text=True, timeout=10,
            )
            for line in result.stdout.strip().splitlines():
                parts = line.split("\t", 1)
                if len(parts) == 2 and parts[1] != tag:
                    stale.append(parts[0])
        except Exception:
            pass

        note = f"Image rebuilt: {tag}"
        if stale:
            note += f". {len(stale)} container(s) still on old image — use [C] Containers to cycle them."
        return {"image": tag, "stale_containers": stale, "note": note}

    def _git_diff(self, params: dict) -> dict:
        import subprocess as sp
        from omni_code.work_cli import _resolve_ticket_workspace
        ticket_id = params["ticket_id"]
        project_id = params.get("project_id")
        try:
            workspace = str(_resolve_ticket_workspace(ticket_id, project_id))
        except Exception as exc:
            raise ValueError(f"Cannot resolve workspace: {exc}")

        result = sp.run(
            ["git", "-C", workspace, "diff", "--numstat"],
            capture_output=True, text=True, timeout=10,
        )
        files = []
        total_add = 0
        total_del = 0
        if result.returncode == 0:
            for line in result.stdout.strip().splitlines():
                parts = line.split("\t", 2)
                if len(parts) != 3:
                    continue
                add_str, del_str, path = parts
                is_binary = add_str == "-"
                additions = int(add_str) if not is_binary else 0
                deletions = int(del_str) if not is_binary else 0
                total_add += additions
                total_del += deletions
                files.append({
                    "path": path,
                    "additions": additions,
                    "deletions": deletions,
                    "is_binary": is_binary,
                })

        # Also include staged changes
        staged = sp.run(
            ["git", "-C", workspace, "diff", "--cached", "--numstat"],
            capture_output=True, text=True, timeout=10,
        )
        if staged.returncode == 0:
            seen = {f["path"] for f in files}
            for line in staged.stdout.strip().splitlines():
                parts = line.split("\t", 2)
                if len(parts) != 3:
                    continue
                add_str, del_str, path = parts
                if path in seen:
                    continue
                is_binary = add_str == "-"
                additions = int(add_str) if not is_binary else 0
                deletions = int(del_str) if not is_binary else 0
                total_add += additions
                total_del += deletions
                files.append({
                    "path": path,
                    "additions": additions,
                    "deletions": deletions,
                    "is_binary": is_binary,
                    "staged": True,
                })

        # Untracked files
        untracked = sp.run(
            ["git", "-C", workspace, "ls-files", "--others", "--exclude-standard"],
            capture_output=True, text=True, timeout=10,
        )
        if untracked.returncode == 0:
            seen = {f["path"] for f in files}
            for line in untracked.stdout.strip().splitlines():
                path = line.strip()
                if path and path not in seen:
                    files.append({
                        "path": path,
                        "additions": 0,
                        "deletions": 0,
                        "is_binary": False,
                        "untracked": True,
                    })

        return {
            "total_files": len(files),
            "total_additions": total_add,
            "total_deletions": total_del,
            "has_changes": len(files) > 0,
            "files": files,
        }

    def _git_diff_file(self, params: dict) -> dict:
        import subprocess as sp
        from omni_code.work_cli import _resolve_ticket_workspace
        ticket_id = params["ticket_id"]
        project_id = params.get("project_id")
        file_path = params["path"]
        workspace = str(_resolve_ticket_workspace(ticket_id, project_id))
        result = sp.run(
            ["git", "-C", workspace, "diff", "--", file_path],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            # Try staged diff
            result = sp.run(
                ["git", "-C", workspace, "diff", "--cached", "--", file_path],
                capture_output=True, text=True, timeout=10,
            )
        return {"patch": result.stdout if result.returncode == 0 else ""}

    # --- Phase 6: Auto-dispatch ---

    def _projects_set_auto_dispatch(self, params: dict) -> None:
        project_id = params["project_id"]
        enabled = bool(params.get("enabled", False))
        config = self._runtime._load()
        for raw in config.get("fleetProjects", []):
            if raw.get("id") == project_id:
                raw["autoDispatch"] = enabled
                break
        self._runtime._save(config)
        return None

    def _projects_get_auto_dispatch(self, params: dict) -> dict:
        project_id = params["project_id"]
        config = self._runtime._load()
        for raw in config.get("fleetProjects", []):
            if raw.get("id") == project_id:
                return {"enabled": bool(raw.get("autoDispatch", False))}
        return {"enabled": False}

    # --- Sandbox config ---

    def _projects_set_sandbox(self, params: dict) -> None:
        """Set or clear the sandbox config for a project.

        params.sandbox can be:
          - null / None  → remove sandbox (run locally)
          - {"image": "..."} → use a pre-built Docker image
          - {"dockerfile": "..."} → use a Dockerfile (relative to workspace)
        """
        project_id = params["project_id"]
        sandbox = params.get("sandbox")  # None means remove
        config = self._runtime._load()
        for raw in config.get("fleetProjects", []):
            if raw.get("id") == project_id:
                if sandbox is None:
                    raw.pop("sandbox", None)
                else:
                    raw["sandbox"] = sandbox
                break
        self._runtime._save(config)
        return None

    def _projects_get_sandbox(self, params: dict) -> dict:
        project_id = params["project_id"]
        config = self._runtime._load()
        for raw in config.get("fleetProjects", []):
            if raw.get("id") == project_id:
                return {"sandbox": raw.get("sandbox")}
        return {"sandbox": None}

    def _get_project_sandbox(self, project_id: str | None) -> dict | None:
        """Helper: resolve the sandbox config dict for a project, or None if local."""
        if not project_id:
            return None
        config = self._runtime._load()
        for raw in config.get("fleetProjects", []):
            if raw.get("id") == project_id:
                return raw.get("sandbox")
        return None

    def _get_sandbox_for_ticket(self, ticket_id: str, project_id: str | None) -> dict | None:
        """Helper: resolve sandbox config for a ticket's project."""
        ticket = self._runtime.resolve_ticket(ticket_id, project_id)
        if ticket is None:
            return None
        return self._get_project_sandbox(ticket.project_id)

    # --- Phase 7: Better artifact browsing ---

    def _ticket_artifact_open(self, params: dict) -> None:
        import subprocess as sp
        from omni_code.work_cli import _ticket_artifacts_dir

        ticket_id = params["ticket_id"]
        file_path = params["path"]
        root = _ticket_artifacts_dir(ticket_id)
        target = (root / file_path).resolve()
        if not str(target).startswith(str(root.resolve())):
            raise ValueError("Path traversal detected")
        if not target.exists():
            raise ValueError(f"Artifact not found: {file_path}")

        import platform
        if platform.system() == "Darwin":
            sp.Popen(["open", str(target)])
        else:
            sp.Popen(["xdg-open", str(target)])
        return None


    # --- MCP server config ---

    def _mcp_config_path(self) -> Path:
        from omni_code.config import get_config_dir
        return get_config_dir() / "mcp.json"

    def _mcp_list(self, params: dict) -> dict:
        path = self._mcp_config_path()
        if not path.is_file():
            return {"mcpServers": {}}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {"mcpServers": {}}

    def _mcp_save(self, params: dict) -> None:
        path = self._mcp_config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        config = params.get("config", {"mcpServers": {}})
        path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return None

    # --- Model connection test ---

    def _models_test(self, params: dict) -> dict:
        """Test model connectivity. Calls omni model test under the hood."""
        import subprocess as sp
        name = params.get("name")
        cmd = ["omni", "model", "test"]
        if name:
            cmd.append(name)
        try:
            result = sp.run(cmd, capture_output=True, text=True, timeout=30)
            output = (result.stdout + result.stderr).strip()
            return {"success": result.returncode == 0, "output": output}
        except sp.TimeoutExpired:
            return {"success": False, "output": "Connection test timed out (30s)"}
        except Exception as exc:
            return {"success": False, "output": str(exc)}

    # --- Onboarding status ---

    def _onboarding_status(self, params: dict) -> dict:
        from omni_code.models import has_models_configured
        return {"models_configured": has_models_configured()}

    # --- Omni code sessions ---

    def _sessions_list(self, params: dict) -> list:
        """List omni code sessions from the sessions database."""
        from omniagents.core.session.query import (
            ensure_sessions_schema,
            query_sessions,
            populate_session_stats,
        )
        project = "omni_code"
        agent = params.get("agent", "omni")
        limit = params.get("limit", 20)
        offset = params.get("offset", 0)
        include_archived = params.get("include_archived", False)

        try:
            ensure_sessions_schema(project=project, agent=agent)
            sessions = query_sessions(
                project=project,
                agent=agent,
                include_archived=include_archived,
                after=None,
                before=None,
                limit=limit,
                offset=offset,
            )
            populate_session_stats(project=project, agent=agent, sessions=sessions)
            return sessions
        except Exception:
            return []

    # --- Project hotkeys ---

    def _projects_get_hotkeys(self, params: dict) -> dict:
        config = self._runtime._load()
        return config.get("fleetProjectHotkeys", {})

    def _projects_set_hotkey(self, params: dict) -> None:
        digit = str(params["digit"])
        project_id = params.get("project_id")  # None to clear
        config = self._runtime._load()
        hotkeys = dict(config.get("fleetProjectHotkeys", {}))
        if project_id is None:
            hotkeys.pop(digit, None)
        else:
            # Remove any existing assignment of this project to another digit
            hotkeys = {k: v for k, v in hotkeys.items() if v != project_id}
            hotkeys[digit] = project_id
        config["fleetProjectHotkeys"] = hotkeys
        self._runtime._save(config)
        return None

    # --- Terminal command resolution (for embedded PTY) ---

    def _terminal_resolve_ssh(self, params: dict) -> dict:
        """Resolve the command to open a shell for a ticket's workspace."""
        from omni_code.work_cli import _resolve_ticket_workspace

        ticket_id = params["ticket_id"]
        project_id = params.get("project_id")
        ticket = self._runtime.resolve_ticket(ticket_id, project_id)
        label = f"SSH: {ticket.title[:30]}" if ticket else f"SSH: {ticket_id[:8]}"
        workspace = _resolve_ticket_workspace(ticket_id, project_id)
        sandbox = self._get_sandbox_for_ticket(ticket_id, project_id)
        shell = os.environ.get("SHELL", "/bin/bash")

        if sandbox:
            from omni_code.work_cli import _ensure_started
            container_name = _ensure_started(workspace, rebuild=False)
            return {"command": ["docker", "exec", "-it", container_name, shell], "label": label}
        else:
            return {"command": [shell], "cwd": str(workspace), "label": label}

    def _terminal_resolve_omni(self, params: dict) -> dict:
        """Resolve the command to run omni for a ticket's workspace."""
        from omni_code.work_cli import _resolve_ticket_workspace

        ticket_id = params["ticket_id"]
        project_id = params.get("project_id")
        ticket = self._runtime.resolve_ticket(ticket_id, project_id)
        label = f"Omni: {ticket.title[:30]}" if ticket else f"Omni: {ticket_id[:8]}"
        workspace = _resolve_ticket_workspace(ticket_id, project_id)
        sandbox = self._get_sandbox_for_ticket(ticket_id, project_id)

        if sandbox:
            from omni_code.work_cli import _ensure_started
            container_name = _ensure_started(workspace, rebuild=False)
            return {"command": ["docker", "exec", "-it", container_name, "omni"], "label": label}
        else:
            return {"command": ["omni"], "cwd": str(workspace), "label": label}

    def _terminal_resolve_chat(self, params: dict) -> dict:
        """Resolve the command to resume a supervisor chat session.

        When auto mode is running, resolves to a read-only log stream so we
        don't conflict with the auto-mode's WebSocket control of the agent.
        Otherwise, if a session ID exists, resumes the interactive session.
        If no session exists, creates one with full supervisor context first.
        """
        from omni_code.work_cli import (
            _build_supervisor_prompt,
            _ensure_started,
            _resolve_ticket_workspace,
            _work_urls,
            _inspect_container,
            _container_name_for_workspace,
        )

        ticket_id = params["ticket_id"]
        project_id = params.get("project_id")

        ticket = self._runtime.resolve_ticket(ticket_id, project_id)
        if not ticket:
            raise ValueError(f"Ticket not found: {ticket_id}")

        project = self._runtime.resolve_project(ticket.project_id)
        label = f"Chat: {ticket.title[:30]}"

        session_id = ticket.supervisor_session_id
        if not session_id:
            task = self._runtime.resolve_task_for_ticket(ticket_id, project_id)
            if task:
                session_id = task.session_id

        workspace = _resolve_ticket_workspace(ticket_id, project_id)
        sandbox = self._get_sandbox_for_ticket(ticket_id, project_id)

        # If no session exists yet, create one with supervisor context so the
        # agent knows about its ticket, pipeline, artifacts dir, etc.
        if not session_id and project and sandbox:
            import asyncio
            from omni_code.work_cli import _rpc_call

            container_name = _ensure_started(workspace, rebuild=False)
            details = _inspect_container(container_name)
            ws_url = _work_urls(details).get("ws_url")
            if ws_url:
                supervisor_prompt = _build_supervisor_prompt(ticket, project, self._runtime)
                self._runtime.write_ticket_file(ticket.id, project.id)
                resp = asyncio.run(_rpc_call(ws_url, "server_call", {
                    "function": "session.ensure",
                    "args": {"variables": {"additional_instructions": supervisor_prompt}},
                }))
                if not resp.get("error"):
                    session_id = str((resp.get("result") or {}).get("session_id") or "")
                    if session_id:
                        self._runtime.update_ticket_fields(
                            ticket.id, {"supervisorSessionId": session_id}, project.id,
                        )

        cmd = ["omni"]
        if session_id:
            cmd.extend(["--session-id", session_id])

        if sandbox:
            container_name = _ensure_started(workspace, rebuild=False)
            return {"command": ["docker", "exec", "-it", container_name] + cmd, "label": label}
        else:
            return {"command": cmd, "cwd": str(workspace), "label": label}

    # --- Project-level terminal resolution (no ticket required) ---

    def _project_resolve_shell(self, params: dict) -> dict:
        """Resolve the command to open a shell for a project's workspace."""
        project_id = params["project_id"]
        project = self._runtime.resolve_project(project_id)
        if not project:
            raise ValueError(f"Project not found: {project_id}")
        workspace = Path(project.workspace_dir)
        sandbox = self._get_project_sandbox(project_id)
        shell = os.environ.get("SHELL", "/bin/bash")
        label = f"Shell: {project.label}"

        if sandbox:
            from omni_code.work_cli import _ensure_started
            container_name = _ensure_started(workspace, rebuild=False)
            return {"command": ["docker", "exec", "-it", container_name, shell], "label": label}
        else:
            return {"command": [shell], "cwd": str(workspace), "label": label}

    def _project_resolve_omni(self, params: dict) -> dict:
        """Resolve the command to run omni for a project's workspace."""
        project_id = params["project_id"]
        project = self._runtime.resolve_project(project_id)
        if not project:
            raise ValueError(f"Project not found: {project_id}")
        workspace = Path(project.workspace_dir)
        sandbox = self._get_project_sandbox(project_id)
        label = f"Omni: {project.label}"

        if sandbox:
            from omni_code.work_cli import _ensure_started
            container_name = _ensure_started(workspace, rebuild=False)
            return {"command": ["docker", "exec", "-it", container_name, "omni"], "label": label}
        else:
            return {"command": ["omni"], "cwd": str(workspace), "label": label}
