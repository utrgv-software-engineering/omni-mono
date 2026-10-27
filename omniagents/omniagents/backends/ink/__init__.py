"""React Ink TUI backend for OmniAgents.

The TUI is a prebuilt bundle (``tui/dist/index.js``) shipped in the wheel and
run with Node.js 22 or newer. A source checkout builds it from the monorepo
root: ``npm ci && npm run build -w omni-ink-tui``.

Environment variables:
- OMNI_INK_TUI_PATH: a prebuilt ``dist/index.js`` to run instead.
- OMNI_DEBUG: if set, enables additional debug logging.
"""

import os
import sys
import subprocess
from pathlib import Path
import shutil
from typing import Callable, Optional
import logging

from omniagents.core.agents.specs import AgentSpec
from omniagents.core.debug import Debug
from omniagents.core.runtime.overrides import apply_cli_overrides


def theme_args(config_dir: Optional[Path] = None) -> list[str]:
    """Where the TUI finds user themes and remembers the chosen one.

    Both live under the product's config directory, the way Omarchy
    drops a generated theme into ``~/.claude/themes`` or
    ``~/.pi/agent/themes``: ``<config>/themes/<name>.toml`` (or
    ``<name>/colors.toml``) in Omarchy's ``colors.toml`` format, and
    ``<config>/ink.json`` for the saved ``theme``. The TUI itself finds
    Omarchy's current theme and follows it when nothing else is chosen;
    ``OMNI_THEME`` in the environment names a theme explicitly.
    """
    if config_dir is None:
        try:
            from omniagents.core.product import product_info

            config_dir = product_info().config_dir()
        except Exception:
            return []
    return [
        "--themes-dir",
        str(config_dir / "themes"),
        "--settings-file",
        str(config_dir / "ink.json"),
    ]


def detach_terminal(log_fd: Optional[int] = None) -> Callable[[], None]:
    """Point this process's fd 0, 1 and 2 away from the terminal.

    stdin goes to ``os.devnull``; stdout and stderr go to ``log_fd`` when
    given (the debug log) and to ``os.devnull`` otherwise. Descendants
    spawned afterwards inherit the redirected descriptors, so none of
    them can read the TUI's keystrokes or draw over its frame. Returns a
    callable that puts the original descriptors back, for the exit
    message printed once the TUI has released the screen.
    """
    saved = {fd: os.dup(fd) for fd in (0, 1, 2)}
    null = os.open(os.devnull, os.O_RDWR)
    try:
        os.dup2(null, 0)
        os.dup2(log_fd if log_fd is not None else null, 1)
        os.dup2(log_fd if log_fd is not None else null, 2)
    finally:
        os.close(null)

    def restore() -> None:
        for fd, copy in saved.items():
            os.dup2(copy, fd)
            os.close(copy)

    return restore


class InkBackend:
    """React Ink TUI backend implementation.

    This backend launches a Node.js-based terminal UI (using React Ink) that
    communicates with the Python agent server via WebSocket/JSON-RPC protocol.
    """

    def run(self, spec: AgentSpec, **kwargs) -> None:
        """Run the Ink backend with the given agent specification.

        Args:
            spec: The agent specification containing configuration,
                  instructions, tools, and other settings
            **kwargs: Additional backend-specific arguments including:
                - config_path: Path to the YAML configuration file
                - auth_token: Optional authentication token
                - debug: Enable debug mode
                - session_id: Resume a specific session
        """
        # Import server dependencies
        try:
            from omniagents.backends.server.app import build_app
            from omniagents.core.utils.serving import BackgroundServer, bind_socket
            import uvicorn
        except ImportError as e:
            print(
                f"Error: Server dependencies not installed. Install with: pip install omniagents[server]"
            )
            print(f"Details: {e}")
            sys.exit(1)

        args = kwargs.get("args")
        apply_cli_overrides(spec, args)

        # Extract configuration from kwargs
        config_path = kwargs.get("config_path")
        auth_token = kwargs.get("auth_token")
        # Get debug from kwargs or from args object
        debug = kwargs.get("debug", False)
        if not debug and args and hasattr(args, "debug"):
            debug = args.debug
        session_id = kwargs.get("session_id")
        resume_mode = bool(kwargs.get("resume"))
        initial_prompt = kwargs.get("initial_prompt")

        # Ink mode does not support voice/realtime, so disable it
        spec.realtime_mode = False
        spec.voice_spec = None

        # Build the FastAPI app with the agent spec.
        # ink auto-launches the server in the user's terminal, so the
        # user's cwd is the workspace_root they implicitly intend. Pass
        # it as the default so builtin tools "just work" without the
        # client having to populate workspace_root explicitly. Production
        # server deployments (omni --mode server) leave this unset and
        # require clients to populate it.
        app = build_app(
            config_path=config_path,
            spec=spec,
            auth_token=auth_token,
            default_host_workspace_root=os.getcwd(),
        )

        # Silence noisy third-party loggers
        for name in (
            "agents",
            "openai",
            "httpx",
            "urllib3",
            "anthropic",
            "litellm",
        ):
            try:
                lg = logging.getLogger(name)
                lg.handlers.clear()
                lg.addHandler(logging.NullHandler())
                lg.setLevel(logging.CRITICAL)
                lg.propagate = False
            except Exception:
                pass

        # The server binds its own port-0 socket; the TUI is pointed at the
        # port it actually listens on once startup has finished.
        try:
            server = BackgroundServer(
                uvicorn.Config(app, log_level="error", access_log=False),
                bind_socket("127.0.0.1", 0),
            ).start()
        except (OSError, RuntimeError) as e:
            print(f"Error: Failed to start server: {e}")
            sys.exit(1)
        port = server.port
        if debug:
            print(f"[DEBUG] Server started successfully on port {port}")

        binary_path = self._find_binary(
            env_override=os.environ.get("OMNI_INK_TUI_PATH")
        )
        if binary_path is None:
            print("Error: the Ink TUI bundle (omni-ink-tui) is missing.")
            print("In a source checkout, build it from the monorepo root:")
            print("  npm ci && npm run build -w omni-ink-tui")
            sys.exit(1)
        node_problem = self._node_problem()
        if node_problem:
            print(f"Error: {node_problem}")
            sys.exit(1)

        # Prepare environment
        env = os.environ.copy()
        if debug:
            env["OMNI_DEBUG"] = "1"

        # Build command line arguments
        server_url = f"ws://127.0.0.1:{port}/ws"

        # Debug output
        if debug:
            print(f"[DEBUG] Server URL: {server_url}")
            print(f"[DEBUG] Binary path: {binary_path}")
            print(f"[DEBUG] Agent name: {spec.name}")

        cmd = [
            "node",
            str(binary_path),
            "--server",
            server_url,
            "--name",
            spec.name or "OmniAgent",
        ]

        # Add optional parameters
        if auth_token:
            cmd.extend(["--token", auth_token])

        if spec.welcome_text:
            cmd.extend(["--welcome", spec.welcome_text])

        if spec.use_safe_agent:
            cmd.append("--safe-mode")

        if session_id:
            cmd.extend(["--session", session_id])

        if resume_mode:
            cmd.append("--resume")

        if initial_prompt:
            cmd.extend(["--initial-message", initial_prompt])

        if debug:
            cmd.append("--debug")

        cmd.extend(theme_args())

        # Launch the Ink TUI with proper stdin/stdout/stderr
        exit_code = 0
        try:
            # Pass stdin/stdout directly so Ink can use raw mode, with
            # stdout as the original (pre-redirect) terminal. The child's
            # stderr goes wherever the parent's captured stderr goes (the
            # debug log, or /dev/null) — node warnings and crash traces
            # must not draw over the live frame either.
            real_stdout = Debug.original_stdout()
            proc = subprocess.Popen(
                cmd,
                env=env,
                stdin=sys.stdin,
                stdout=real_stdout,
                stderr=sys.stderr,
            )
            # The TUI now holds its own copies of the terminal. This
            # process has no further use for it, and every tool command
            # it spawns from here on would inherit whatever it keeps, so
            # give the terminal up at the descriptor level, not just the
            # sys.stdout object the Debug capture swaps.
            restore_terminal = detach_terminal(Debug.log_fileno())
            try:
                exit_code = proc.wait()
            finally:
                restore_terminal()
        except KeyboardInterrupt:
            pass
        except FileNotFoundError:
            print(f"Error: Command not found: {cmd[0]}")
            print("Please ensure Node.js is installed.")
            sys.exit(127)
        except Exception as e:
            print(f"Error launching TUI: {e}")
            sys.exit(1)

        if exit_code != 0 and exit_code != -2:
            print(
                f"Error: omni-ink-tui exited with code {exit_code} "
                "(re-run with --debug and see ~/.omniagents-debug.log "
                "for its stderr)",
                file=Debug.original_stderr(),
            )
            sys.exit(exit_code)

    def _find_binary(self, env_override: Optional[str] = None) -> Optional[Path]:
        if env_override:
            p = Path(env_override).expanduser()
            if p.is_file():
                return p
        packaged = Path(__file__).parent / "tui" / "dist" / "index.js"
        return packaged if packaged.is_file() else None

    def _node_problem(self) -> Optional[str]:
        """Why Node.js cannot run the TUI, or None when it can. The bundle
        relies on Node 22's built-in WebSocket."""
        if shutil.which("node") is None:
            return (
                "Node.js 22 or newer is required. Install it from https://nodejs.org/"
            )
        try:
            version = subprocess.check_output(["node", "-v"], text=True).strip()
        except (OSError, subprocess.SubprocessError) as e:
            return f"could not run node: {e}"
        major = version.lstrip("v").split(".")[0]
        if not major.isdigit() or int(major) < 22:
            return f"Node.js 22 or newer is required (found {version})."
        return None


__all__ = ["InkBackend"]
