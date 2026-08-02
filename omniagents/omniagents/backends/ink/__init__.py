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
from omniagents.core.debug import Debug
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

        env_override = os.environ.get("OMNI_INK_TUI_PATH")
        binary_path = self._find_binary(env_override=env_override)
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

        # Launch the Ink TUI with proper stdin/stdout/stderr
        exit_code = 0
        try:
            # Pass stdin/stdout/stderr directly so Ink can use raw mode.
            # Use the original stdout (pre-redirect) so the TUI renders
            # to the real terminal even when stdout has been captured.
            real_stdout = Debug.original_stdout()
            proc = subprocess.Popen(
                cmd,
                env=env,
                stdin=sys.stdin,
                stdout=real_stdout,
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

    def _find_binary(self, env_override: Optional[str] = None) -> Optional[Path]:
        if env_override:
            p = Path(env_override).expanduser()
            if p.exists() and p.is_file():
                return p
        # Check source directory first (for development)
        base = Path(__file__).parent
        dist_path = base / "tui" / "dist" / "index.js"
        if dist_path.exists() and dist_path.is_file():
            return dist_path
        # Fall back to cache directory (for installed packages)
        cache_dir = self._cache_dir()
        if cache_dir is not None:
            dist_path = cache_dir / "dist" / "index.js"
            if dist_path.exists() and dist_path.is_file():
                return dist_path
        return None

    def _build_binary(self, debug: bool = False) -> Path:
        if shutil.which("npm") is None or shutil.which("node") is None:
            print(
                "Error: Node.js/npm not found. Install Node.js from https://nodejs.org/ and retry."
            )
            raise SystemExit(1)
        try:
            v = subprocess.check_output(["node", "-v"], text=True).strip()
            if v.startswith("v"):
                v = v[1:]
            parts = v.split(".")
            major = int(parts[0]) if parts and parts[0].isdigit() else 0
            if major < 18:
                print("Error: Node.js >= 18 is required. Please upgrade.")
                raise SystemExit(1)
        except Exception:
            pass

        src_base = Path(__file__).parent / "tui"
        if not src_base.exists():
            print("Error: Source directory for omni-ink-tui not found.")
            raise SystemExit(1)

        cache_dir = self._cache_dir()
        if cache_dir is None:
            cache_dir = src_base

        src_hash = self._compute_source_hash(src_base)
        stamp = self._stamp_path(cache_dir)
        need_sync = True
        try:
            if stamp.exists():
                prev = stamp.read_text(encoding="utf-8").strip()
                if src_hash and prev and prev == src_hash:
                    need_sync = False
        except Exception:
            need_sync = True

        if need_sync and cache_dir != src_base:
            if cache_dir.exists():
                try:
                    shutil.rmtree(cache_dir)
                except Exception:
                    pass
            try:
                shutil.copytree(src_base, cache_dir)
            except Exception as e:
                print(f"Error: Failed to prepare cache directory: {e}")
                raise SystemExit(1)

        env = os.environ.copy()
        env.setdefault("NPM_CONFIG_AUDIT", "false")
        env.setdefault("NPM_CONFIG_FUND", "false")
        if debug:
            print(f"[DEBUG] Building omni-ink-tui in {cache_dir}")

        install_cmd = ["npm", "install"]
        lockfile = cache_dir / "package-lock.json"
        if lockfile.exists():
            install_cmd = ["npm", "ci"]

        try:
            if debug:
                print("[DEBUG] Running " + " ".join(install_cmd))
            proc = subprocess.run(
                install_cmd,
                cwd=str(cache_dir),
                check=True,
                env=env,
                stdout=None if debug else subprocess.PIPE,
                stderr=None if debug else subprocess.PIPE,
                text=True,
            )
        except subprocess.CalledProcessError as e:
            msg = f"Error: {' '.join(install_cmd)} failed with exit code {e.returncode}"
            try:
                out = e.stdout or ""
                err = e.stderr or ""
                tail = (out + "\n" + err).splitlines()[-50:]
                if tail:
                    print(msg)
                    print("\n".join(tail))
                else:
                    print(msg)
            except Exception:
                print(msg)
            raise SystemExit(e.returncode)

        try:
            if debug:
                print("[DEBUG] Running npm run build...")
            proc2 = subprocess.run(
                ["npm", "run", "build"],
                cwd=str(cache_dir),
                check=True,
                env=env,
                stdout=None if debug else subprocess.PIPE,
                stderr=None if debug else subprocess.PIPE,
                text=True,
            )
        except subprocess.CalledProcessError as e:
            msg = f"Error: npm run build failed with exit code {e.returncode}"
            try:
                out = e.stdout or ""
                err = e.stderr or ""
                tail = (out + "\n" + err).splitlines()[-50:]
                if tail:
                    print(msg)
                    print("\n".join(tail))
                else:
                    print(msg)
            except Exception:
                print(msg)
            raise SystemExit(e.returncode)

        out_path = cache_dir / "dist" / "index.js"
        if not out_path.exists():
            print("Error: Build succeeded but output file not found.")
            raise SystemExit(1)
        try:
            if src_hash:
                stamp.write_text(src_hash + "\n", encoding="utf-8")
        except Exception:
            pass
        return out_path

    def _cache_dir(self) -> Optional[Path]:
        base = self._user_cache_dir()
        if base is None:
            return None
        dest = base / "ink" / "tui"
        dest.mkdir(parents=True, exist_ok=True)
        return dest

    def _user_cache_dir(self) -> Optional[Path]:
        home = Path.home()
        system = platform.system().lower()
        if system == "windows":
            base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
            if not base:
                return None
            return Path(base) / "OmniAgents"
        elif system == "darwin":
            return home / "Library" / "Caches" / "OmniAgents"
        else:
            xdg = os.environ.get("XDG_CACHE_HOME")
            if xdg:
                return Path(xdg) / "omniagents"
            return home / ".cache" / "omniagents"

    def _stamp_path(self, cache_dir: Path) -> Path:
        return cache_dir / "omni-ink-tui.hash"

    def _compute_source_hash(self, src_base: Path) -> Optional[str]:
        try:
            files = []
            for root, _dirs, names in os.walk(src_base / "src"):
                for n in names:
                    if n.endswith(".ts") or n.endswith(".tsx"):
                        files.append(str(Path(root) / n))
            files.append(str(src_base / "package.json"))
            files.append(str(src_base / "tsconfig.json"))
            tsx_cfg = src_base / "tsconfig.tsx.json"
            if tsx_cfg.exists():
                files.append(str(tsx_cfg))
            files = [f for f in files if os.path.isfile(f)]
            files.sort()
            import hashlib as _hashlib

            digest = _hashlib.sha256()
            for f in files:
                with open(f, "rb") as fh:
                    digest.update(fh.read())
            return digest.hexdigest()
        except Exception:
            return None


__all__ = ["InkBackend"]
