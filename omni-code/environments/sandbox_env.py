"""Docker sandbox evaluation environment.

Spins up an omni-code sandbox container per scenario, sends prompts
via the WebSocket JSON-RPC API, and collects session history from the
bind-mounted workspace for measure evaluation.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from omniagents.core.eval.environment import EnvironmentContext, EvalEnvironment

_logger = logging.getLogger(__name__)

_DEFAULT_FIXTURES_DIR = (
    Path(__file__).resolve().parent.parent / "evaluations" / "fixtures" / "workspaces"
)
_DEFAULT_IMAGE = "omni-code-sandbox:latest"
_DEFAULT_TIMEOUT = 180  # seconds per scenario
_CONTAINER_PORT = 7681


class SandboxEvalEnvironment(EvalEnvironment):
    """Evaluation environment that runs the agent inside a Docker container.

    Each scenario gets its own container with a bind-mounted temp workspace.
    Network isolation is enabled by default (blocks all outbound except the
    model API endpoint). The agent runs in server mode and is controlled
    via WebSocket JSON-RPC.
    """

    def __init__(
        self,
        image: str = _DEFAULT_IMAGE,
        network_allowlist: str = "",
        timeout_seconds: int = _DEFAULT_TIMEOUT,
        fixtures_dir: str | None = None,
        model_api_host: str | None = None,
        **kwargs: Any,
    ) -> None:
        self.image = image
        self.network_allowlist = network_allowlist
        self.timeout_seconds = timeout_seconds
        self.fixtures_dir = Path(fixtures_dir) if fixtures_dir else _DEFAULT_FIXTURES_DIR
        self.model_api_host = model_api_host

    async def setup(self, scenario: Dict[str, Any]) -> EnvironmentContext:
        session_id = str(uuid.uuid4())
        tmp_dir = tempfile.mkdtemp(prefix="omni_sandbox_eval_")

        # Copy fixture workspace
        fixture_name = scenario.get("fixture")
        if fixture_name:
            fixture_src = self.fixtures_dir / fixture_name
            if fixture_src.is_dir():
                shutil.copytree(fixture_src, tmp_dir, dirs_exist_ok=True)

        # Write inline AGENTS.md if provided
        agents_md_content = scenario.get("agents_md_content")
        if agents_md_content:
            (Path(tmp_dir) / "AGENTS.md").write_text(agents_md_content)

        # Find a free host port
        host_port = _pick_free_port()
        container_name = f"omni-eval-{session_id[:8]}"

        # Build docker run command
        cmd = [
            "docker", "run", "-d", "--rm",
            "--name", container_name,
            "-p", f"{host_port}:{_CONTAINER_PORT}",
            "-v", f"{tmp_dir}:/home/user/workspace",
        ]

        # Network isolation: allow model API + nothing else
        allowlist = self.network_allowlist
        if self.model_api_host and allowlist:
            allowlist = f"{allowlist},{self.model_api_host}"
        elif self.model_api_host:
            allowlist = self.model_api_host

        if allowlist:
            cmd.extend(["--cap-add", "NET_ADMIN"])
            cmd.extend(["-e", f"OMNI_SANDBOX_NETWORK_ALLOWLIST={allowlist}"])

        # Pass through API keys from host environment
        import os

        for key in [
            "OPENAI_API_KEY",
            "OPENAI_BASE_URL",
            "AZURE_OPENAI_API_KEY",
            "AZURE_OPENAI_ENDPOINT",
            "AZURE_OPENAI_API_VERSION",
            "ANTHROPIC_API_KEY",
            "GOOGLE_API_KEY",
            "MODEL_NAME",
        ]:
            if os.environ.get(key):
                cmd.extend(["-e", key])

        cmd.extend([
            self.image,
            "omni", "--mode", "server",
            "--host", "0.0.0.0", "--port", str(_CONTAINER_PORT),
        ])

        # Start container
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(
                f"Failed to start sandbox container: {stderr.decode().strip()}"
            )
        container_id = stdout.decode().strip()

        # Wait for WebSocket server to be ready
        ws_url = f"ws://localhost:{host_port}/ws"
        await _wait_for_server(ws_url, timeout=30)

        return EnvironmentContext(
            session_id=session_id,
            data={
                "workspace_root": tmp_dir,
                "fixture": fixture_name,
                "tmp_dir": tmp_dir,
                "container_name": container_name,
                "container_id": container_id,
                "host_port": host_port,
                "ws_url": ws_url,
            },
        )

    async def teardown(self, context: EnvironmentContext) -> None:
        container_name = context.get("container_name")
        if container_name:
            proc = await asyncio.create_subprocess_exec(
                "docker", "rm", "-f", container_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()

        tmp_dir = context.get("tmp_dir")
        if tmp_dir and Path(tmp_dir).exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)

    async def verify(
        self, context: EnvironmentContext, expected: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Verify filesystem state on the bind-mounted workspace."""
        tmp_dir = Path(context.get("workspace_root", ""))
        results: Dict[str, Any] = {}
        success = True

        for rel_path, expectations in expected.items():
            full_path = tmp_dir / rel_path
            expect_exists = expectations.get("exists", True)

            if expect_exists:
                if not full_path.exists():
                    results[rel_path] = {"matched": False, "reason": "file not found"}
                    success = False
                    continue
                content = full_path.read_text(errors="replace")
                for pattern in expectations.get("contains", []):
                    if pattern not in content:
                        results[rel_path] = {
                            "matched": False,
                            "reason": f"missing: {pattern!r}",
                        }
                        success = False
                        break
                else:
                    results[rel_path] = {"matched": True}
            else:
                if full_path.exists():
                    results[rel_path] = {"matched": False, "reason": "should not exist"}
                    success = False
                else:
                    results[rel_path] = {"matched": True}

        return {"success": success, "results": results}


# ── Sandbox interaction helpers ─────────────────────────────────────


async def run_prompt_in_sandbox(
    ws_url: str,
    prompt: str,
    timeout: float = 180,
    auto_approve: bool = True,
) -> Dict[str, Any]:
    """Send a prompt to a running sandbox agent and wait for completion.

    Returns dict with session_id, run_id, and history.
    """
    import websockets

    async with websockets.connect(ws_url, max_size=None) as ws:
        # Start a run with all tools auto-approved
        start_params: dict = {"prompt": prompt}
        if auto_approve:
            start_params["safe_tool_overrides"] = {"safe_tool_patterns": [".*"]}

        await _ws_send(ws, "start_run", start_params, request_id="start")

        session_id: Optional[str] = None
        run_id: Optional[str] = None
        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
            except asyncio.TimeoutError:
                continue

            msg = json.loads(raw)

            # RPC response for start_run
            if msg.get("id") == "start":
                result = msg.get("result", {})
                session_id = result.get("session_id")
                run_id = result.get("run_id")
                continue

            # Server notification
            method = msg.get("method", "")

            # Auto-approve tool calls if requested
            if auto_approve and method == "client_request":
                params = msg.get("params", {})
                req_id = params.get("request_id")
                if req_id and params.get("function") == "ui.request_tool_approval":
                    await _ws_send(
                        ws,
                        "client_response",
                        {"request_id": req_id, "result": {"approved": True}},
                        request_id=f"approve-{req_id}",
                    )
                continue

            # Run finished
            if method in ("run_end", "run_finished"):
                break

        # Collect history
        if session_id:
            await _ws_send(
                ws,
                "get_session_history",
                {"session_id": session_id},
                request_id="history",
            )
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
                msg = json.loads(raw)
                if msg.get("id") == "history":
                    history = msg.get("result", [])
                    return {
                        "session_id": session_id,
                        "run_id": run_id,
                        "history": history,
                    }

        return {"session_id": session_id, "run_id": run_id, "history": []}


async def _ws_send(ws: Any, method: str, params: dict, request_id: str) -> None:
    await ws.send(
        json.dumps({
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        })
    )


async def _wait_for_server(ws_url: str, timeout: float = 30) -> None:
    """Poll until the WebSocket server accepts connections."""
    import websockets

    deadline = time.monotonic() + timeout
    last_err: Optional[Exception] = None

    while time.monotonic() < deadline:
        try:
            async with websockets.connect(ws_url, max_size=None) as ws:
                # Send a simple RPC to verify the server is ready
                await _ws_send(ws, "get_agent_info", {}, request_id="ping")
                raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                msg = json.loads(raw)
                if msg.get("id") == "ping":
                    return
        except Exception as exc:
            last_err = exc
            await asyncio.sleep(1.0)

    raise RuntimeError(
        f"Sandbox server at {ws_url} did not become ready within {timeout}s: {last_err}"
    )


def _pick_free_port() -> int:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        return int(s.getsockname()[1])
