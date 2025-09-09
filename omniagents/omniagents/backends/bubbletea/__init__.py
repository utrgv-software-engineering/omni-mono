"""Bubble Tea TUI backend for OmniAgents.

This backend can auto-build the omni-tui Go binary on first run when the
binary is not found and a Go toolchain is available. If Go is not installed,
it will instruct the user how to install it.

Environment variables:
- OMNI_TUI_AUTO_BUILD: if set to "0" or "false", disables auto-build.
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

from omniagents.core.agents.specs import AgentSpec
import hashlib
from glob import glob


class BubbleTeaBackend:
    """Bubble Tea TUI backend implementation.
    
    This backend launches a Go-based terminal UI that communicates with
    the Python agent server via WebSocket/JSON-RPC protocol.
    """
    
    def run(self, spec: AgentSpec, **kwargs) -> None:
        """Run the Bubble Tea backend with the given agent specification.
        
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
            print(f"Error: Server dependencies not installed. Install with: pip install omniagents[server]")
            print(f"Details: {e}")
            sys.exit(1)
        
        # Find an available port
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('', 0))
            port = s.getsockname()[1]
        
        # Extract configuration from kwargs
        config_path = kwargs.get('config_path')
        auth_token = kwargs.get('auth_token')
        debug = kwargs.get('debug', False)
        session_id = kwargs.get('session_id')
        
        # Build the FastAPI app with the agent spec
        app = build_app(config_path=config_path, spec=spec, auth_token=auth_token)
        
        # Start server in background thread
        def run_server():
            """Run the FastAPI server in a background thread."""
            uvicorn.run(
                app, 
                host="127.0.0.1", 
                port=port, 
                log_level="error",  # Suppress server logs in TUI
                access_log=False,   # Disable access logs
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
                    s.connect(('127.0.0.1', port))
                    s.close()
                    if debug:
                        print(f"[DEBUG] Server started successfully on port {port}")
                    break
            except (socket.timeout, ConnectionRefusedError):
                if i == max_retries - 1:
                    print(f"Error: Failed to start server on port {port} after {max_retries} attempts")
                    sys.exit(1)
                if debug:
                    print(f"[DEBUG] Waiting for server to start... attempt {i+1}/{max_retries}")
        
        # Find or build the Go binary
        binary_path = self._find_binary()
        cache_dir = self._user_cache_dir()
        source_hash = self._compute_source_hash()
        if not binary_path:
            # Attempt auto-build if allowed
            auto_build = os.environ.get("OMNI_TUI_AUTO_BUILD", "1").lower() not in {"0", "false", "no"}
            if auto_build:
                try:
                    binary_path = self._build_binary(debug=debug)
                except SystemExit:
                    raise
                except Exception as e:
                    print(f"Error: Failed to build omni-tui binary automatically: {e}")
                    sys.exit(1)

        # If we found a cached binary, ensure it matches current source; otherwise rebuild
        if binary_path and cache_dir and binary_path.parent.resolve() == cache_dir.resolve():
            cached_hash = self._read_cached_hash(cache_dir)
            if source_hash and cached_hash and cached_hash != source_hash:
                if debug:
                    print("[DEBUG] TUI source changed; rebuilding cached binary")
                binary_path = self._build_binary(debug=debug)
            elif source_hash and not cached_hash:
                # No stamp present; rebuild to be safe
                if debug:
                    print("[DEBUG] No build stamp found; rebuilding cached binary")
                binary_path = self._build_binary(debug=debug)

        if not binary_path or not binary_path.exists():
            print("Error: omni-tui binary not found.")
            print("We tried to auto-build it but failed or auto-build is disabled.")
            print("\nManual build instructions:")
            print("  cd omniagents/backends/bubbletea/cmd/omni-tui")
            out_name = self._binary_filename()
            print(f"  go build -o ../../bin/{out_name}")
            print("\nIf you don't have Go installed, download it from https://go.dev/dl/")
            sys.exit(1)
        
        # Prepare environment
        env = os.environ.copy()
        if debug:
            env['OMNI_DEBUG'] = '1'
        
        # Build command line arguments
        server_url = f'ws://127.0.0.1:{port}/ws'
        
        # Debug output
        if debug:
            print(f"[DEBUG] Server URL: {server_url}")
            print(f"[DEBUG] Binary path: {binary_path}")
            print(f"[DEBUG] Agent name: {spec.name}")
        
        cmd = [
            str(binary_path),
            '--server', server_url,
            '--name', spec.name or "OmniAgent",
        ]
        
        # Add optional parameters
        if auth_token:
            cmd.extend(['--token', auth_token])
        
        if spec.welcome_text:
            cmd.extend(['--welcome', spec.welcome_text])
        
        if spec.use_safe_agent:
            cmd.append('--safe-mode')
        
        if session_id:
            cmd.extend(['--session', session_id])
        
        if debug:
            cmd.append('--debug')
        
        # Launch the Bubble Tea UI
        tried_rebuild = False
        while True:
            try:
                # Run the Go binary and wait for it to complete
                result = subprocess.run(cmd, env=env)
                # Check if the process exited with an error
                if result.returncode != 0 and result.returncode != -2:  # -2 is Ctrl+C
                    print(f"Error: omni-tui exited with code {result.returncode}")
                    sys.exit(result.returncode)
                break
            except KeyboardInterrupt:
                # User pressed Ctrl+C, exit cleanly
                break
            except FileNotFoundError:
                print(f"Error: Binary not found at {binary_path}")
                print("Attempting to build it locally...")
            except OSError as e:
                # Handle Exec format error (wrong-arch binary)
                if e.errno == errno.ENOEXEC and not tried_rebuild:
                    print("The existing omni-tui binary is not compatible with your platform. Rebuilding...")
                else:
                    print(f"Error launching TUI: {e}")
                    sys.exit(1)

            # Try to rebuild once if we hit a file-not-found or exec format error
            if not tried_rebuild:
                binary_path = self._build_binary(debug=debug)
                cmd[0] = str(binary_path)
                tried_rebuild = True
                continue
            else:
                print("Error: Failed to launch omni-tui even after rebuild.")
                sys.exit(1)
    
    def _find_binary(self) -> Optional[Path]:
        """Find the omni-tui binary.
        
        Searches in the following locations:
        1. User cache directory (preferred, auto-build target)
        2. Compiled binary in package bin/ directory
        3. Development binary in cmd/omni-tui/ directory
        4. System PATH
        
        Returns:
            Path to the binary if found, None otherwise
        """
        # Base directory (where this file is located)
        base = Path(__file__).parent
        name = self._binary_filename()

        # 1) User cache location
        cache_dir = self._user_cache_dir()
        if cache_dir:
            cache_binary = cache_dir / name
            if cache_binary.exists() and cache_binary.is_file():
                return cache_binary
        
        # 2) Package bin directory
        bin_binary = base / "bin" / name
        if bin_binary.exists() and bin_binary.is_file():
            return bin_binary
        
        # 3) Development location
        dev_binary = base / "cmd" / "omni-tui" / name
        if dev_binary.exists() and dev_binary.is_file():
            return dev_binary
        
        # 4) System PATH
        system_binary = shutil.which("omni-tui")
        if system_binary:
            return Path(system_binary)
        
        return None

    def _binary_filename(self) -> str:
        """Return the platform-specific binary filename."""
        return "omni-tui.exe" if platform.system().lower() == "windows" else "omni-tui"

    def _user_cache_dir(self) -> Optional[Path]:
        """Return a suitable per-user cache directory for the binary."""
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
            # Linux and others
            xdg = os.environ.get("XDG_CACHE_HOME")
            if xdg:
                return Path(xdg) / "omniagents"
            return home / ".cache" / "omniagents"

    def _build_binary(self, debug: bool = False) -> Path:
        """Attempt to build the omni-tui binary using the local Go toolchain.

        Returns:
            Path to the built binary.
        Raises:
            SystemExit if Go is not installed or build fails.
        """
        if shutil.which("go") is None:
            print("Error: Go toolchain not found. Install Go from https://go.dev/dl/ and retry.")
            raise SystemExit(1)

        base = Path(__file__).parent
        workdir = base / "cmd" / "omni-tui"
        if not workdir.exists():
            print("Error: Source directory for omni-tui not found.")
            raise SystemExit(1)

        out_dir = self._user_cache_dir() or base / "bin"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / self._binary_filename()

        env = os.environ.copy()
        if debug:
            print(f"[DEBUG] Building omni-tui via Go in {workdir}")
            print(f"[DEBUG] Output: {out_path}")

        # Build command
        build_cmd = [
            "go", "build", "-ldflags", "-s -w", "-o", str(out_path)
        ]

        # Optional: ensure modules are fetched. go build will fetch as needed,
        # but we can explicitly run 'go mod download' to give clearer errors.
        try:
            subprocess.run(["go", "mod", "download"], cwd=str(workdir), check=True, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except Exception:
            # Ignore; go build will try anyway and show errors if needed
            pass

        try:
            result = subprocess.run(build_cmd, cwd=str(workdir), env=env)
            if result.returncode != 0:
                print("Error: 'go build' failed. Ensure Go is installed and reachable, and you have internet to fetch modules.")
                raise SystemExit(result.returncode)
        except FileNotFoundError:
            print("Error: Go not found on PATH. Install from https://go.dev/dl/")
            raise SystemExit(1)

        if not out_path.exists():
            print("Error: Build succeeded but output binary not found.")
            raise SystemExit(1)

        # Ensure executable permissions (especially on POSIX)
        try:
            os.chmod(out_path, 0o755)
        except Exception:
            pass

        # Write/refresh build stamp for source integrity
        try:
            cache_dir = out_dir
            current_hash = self._compute_source_hash()
            if current_hash:
                self._write_cached_hash(cache_dir, current_hash)
        except Exception:
            if debug:
                print("[DEBUG] Failed to write build stamp; continuing")

        return out_path

    # -------------------
    # Hash stamp helpers
    # -------------------
    def _compute_source_hash(self) -> str | None:
        """Compute a SHA256 over the TUI source files we ship.

        Includes: all .go files, go.mod, go.sum, and theme files under cmd/omni-tui.
        Returns a hex digest string.
        """
        try:
            base = Path(__file__).parent
            src_dir = base / "cmd" / "omni-tui"
            if not src_dir.exists():
                return None
            files = []
            files.extend(glob(str(src_dir / "*.go")))
            files.extend(glob(str(src_dir / "themes" / "**"), recursive=True))
            files.append(str(src_dir / "go.mod"))
            files.append(str(src_dir / "go.sum"))
            # Filter to files only
            files = [f for f in files if os.path.isfile(f)]
            files.sort()
            digest = hashlib.sha256()
            for f in files:
                with open(f, "rb") as fh:
                    digest.update(fh.read())
            return digest.hexdigest()
        except Exception:
            return None

    def _stamp_path(self, cache_dir: Path) -> Path:
        return cache_dir / "omni-tui.hash"

    def _read_cached_hash(self, cache_dir: Path) -> str | None:
        try:
            path = self._stamp_path(cache_dir)
            if path.exists():
                return path.read_text(encoding="utf-8").strip()
        except Exception:
            return None
        return None

    def _write_cached_hash(self, cache_dir: Path, value: str) -> None:
        path = self._stamp_path(cache_dir)
        path.write_text(value + "\n", encoding="utf-8")


__all__ = ['BubbleTeaBackend']
