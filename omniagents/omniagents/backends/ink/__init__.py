"""React Ink TUI backend for OmniAgents.

This backend can auto-build the omni-ink-tui binary on first run when the
binary is not found and Node.js/npm is available. If Node.js is not installed,
it will instruct the user how to install it.

Environment variables:
- OMNI_INK_AUTO_BUILD: if set to "0" or "false", disables auto-build.
- OMNI_DEBUG: if set, enables additional debug logging.
"""

import os
import sys
import subprocess
import socket
import time
from pathlib import Path
import platform
import errno
import shutil
from threading import Thread
from typing import Optional
import logging

from omniagents.core.agents.specs import AgentSpec
from omniagents.core.runtime.overrides import apply_cli_overrides


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
            import uvicorn
        except ImportError as e:
            print(
                f"Error: Server dependencies not installed. Install with: pip install omniagents[server]"
            )
            print(f"Details: {e}")
            sys.exit(1)

        # Find an available port
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("", 0))
            port = s.getsockname()[1]

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

        # Build the FastAPI app with the agent spec
        app = build_app(config_path=config_path, spec=spec, auth_token=auth_token)

        # Start server in background thread
        def run_server():
            """Run the FastAPI server in a background thread."""
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
            uvicorn.run(
                app,
                host="127.0.0.1",
                port=port,
                log_level="error",
                access_log=False,
            )

        server_thread = Thread(target=run_server, daemon=True)
        server_thread.start()

        # Give server time to start up and verify it's running
        max_retries = 10
        for i in range(max_retries):
            time.sleep(0.5)
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.settimeout(1)
                    s.connect(("127.0.0.1", port))
                    s.close()
                    if debug:
                        print(f"[DEBUG] Server started successfully on port {port}")
                    break
            except (socket.timeout, ConnectionRefusedError):
                if i == max_retries - 1:
                    print(
                        f"Error: Failed to start server on port {port} after {max_retries} attempts"
                    )
                    sys.exit(1)
                if debug:
                    print(
                        f"[DEBUG] Waiting for server to start... attempt {i+1}/{max_retries}"
                    )

        # Find or build the Ink TUI
        binary_path = self._find_binary()
        if not binary_path:
            # Attempt auto-build if allowed
            auto_build = os.environ.get("OMNI_INK_AUTO_BUILD", "1").lower() not in {
                "0",
                "false",
                "no",
            }
            if auto_build:
                try:
                    binary_path = self._build_binary(debug=debug)
                except SystemExit:
                    raise
                except Exception as e:
                    print(f"Error: Failed to build omni-ink-tui: {e}")
                    sys.exit(1)

        if not binary_path or not binary_path.exists():
            print("Error: omni-ink-tui not found.")
            print("We tried to auto-build it but failed or auto-build is disabled.")
            print("\nManual build instructions:")
            print("  cd omniagents/backends/ink/tui")
            print("  npm install")
            print("  npm run build")
            print(
                "\nIf you don't have Node.js installed, download it from https://nodejs.org/"
            )
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

        # Run the built TUI with node
        cmd = ["node", str(binary_path), "--server", server_url, "--name", spec.name or "OmniAgent"]

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

        # Launch the Ink TUI with proper stdin/stdout/stderr
        exit_code = 0
        try:
            # Pass stdin/stdout/stderr directly so Ink can use raw mode
            proc = subprocess.Popen(
                cmd,
                env=env,
                stdin=sys.stdin,
                stdout=sys.stdout,
                stderr=sys.stderr,
            )
            exit_code = proc.wait()
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
            print(f"Error: omni-ink-tui exited with code {exit_code}")
            sys.exit(exit_code)

    def _find_binary(self) -> Optional[Path]:
        """Find the built omni-ink-tui binary.

        Returns:
            Path to dist/index.js if found, None otherwise
        """
        base = Path(__file__).parent
        dist_path = base / "tui" / "dist" / "index.js"
        if dist_path.exists() and dist_path.is_file():
            return dist_path
        return None

    def _build_binary(self, debug: bool = False) -> Path:
        """Build the omni-ink-tui using npm.

        Returns:
            Path to the built binary
        Raises:
            SystemExit if Node.js/npm is not installed or build fails
        """
        if shutil.which("npm") is None:
            print(
                "Error: npm not found. Install Node.js from https://nodejs.org/ and retry."
            )
            raise SystemExit(1)

        base = Path(__file__).parent
        workdir = base / "tui"
        if not workdir.exists():
            print("Error: Source directory for omni-ink-tui not found.")
            raise SystemExit(1)

        env = os.environ.copy()
        if debug:
            print(f"[DEBUG] Building omni-ink-tui in {workdir}")

        # Install dependencies
        try:
            if debug:
                print("[DEBUG] Running npm install...")
            subprocess.run(
                ["npm", "install"],
                cwd=str(workdir),
                check=True,
                env=env,
                stdout=subprocess.PIPE if not debug else None,
                stderr=subprocess.PIPE if not debug else None,
            )
        except subprocess.CalledProcessError as e:
            print(f"Error: npm install failed with exit code {e.returncode}")
            raise SystemExit(e.returncode)

        # Build the project
        try:
            if debug:
                print("[DEBUG] Running npm run build...")
            subprocess.run(
                ["npm", "run", "build"],
                cwd=str(workdir),
                check=True,
                env=env,
                stdout=subprocess.PIPE if not debug else None,
                stderr=subprocess.PIPE if not debug else None,
            )
        except subprocess.CalledProcessError as e:
            print(f"Error: npm run build failed with exit code {e.returncode}")
            raise SystemExit(e.returncode)

        # Check that the output exists
        out_path = workdir / "dist" / "index.js"
        if not out_path.exists():
            print("Error: Build succeeded but output file not found.")
            raise SystemExit(1)

        return out_path


__all__ = ["InkBackend"]
