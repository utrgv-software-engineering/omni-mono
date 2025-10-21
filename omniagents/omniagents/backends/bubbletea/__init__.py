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
from typing import Iterable, Optional
import logging
import tarfile
import zipfile

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
            print(
                f"Error: Server dependencies not installed. Install with: pip install omniagents[server]"
            )
            print(f"Details: {e}")
            sys.exit(1)

        # Find an available port
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("", 0))
            port = s.getsockname()[1]

        # Extract configuration from kwargs
        config_path = kwargs.get("config_path")
        auth_token = kwargs.get("auth_token")
        debug = kwargs.get("debug", False)
        session_id = kwargs.get("session_id")

        # Build the FastAPI app with the agent spec
        app = build_app(config_path=config_path, spec=spec, auth_token=auth_token)

        # Start server in background thread
        def run_server():
            """Run the FastAPI server in a background thread."""
            # Minimal safeguard: silence noisy third-party loggers so their
            # output cannot corrupt the Bubble Tea TUI. We keep uvicorn quiet
            # via log_level/access_log below, but some SDKs emit via root loggers.
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
                    # Best-effort; never crash server startup on logging config
                    pass
            uvicorn.run(
                app,
                host="127.0.0.1",
                port=port,
                log_level="error",  # Suppress server logs in TUI
                access_log=False,  # Disable access logs
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

        # Find or build the Go binary
        binary_path = self._find_binary()
        cache_dir = self._user_cache_dir()
        source_hash = self._compute_source_hash()
        if not binary_path:
            # Attempt auto-build if allowed
            auto_build = os.environ.get("OMNI_TUI_AUTO_BUILD", "1").lower() not in {
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
                    print(f"Error: Failed to build omni-tui binary automatically: {e}")
                    sys.exit(1)

        # If we found a cached binary, ensure it matches current source; otherwise rebuild
        if (
            binary_path
            and cache_dir
            and binary_path.parent.resolve() == cache_dir.resolve()
        ):
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
            print(
                "\nIf you don't have Go installed, download it from https://go.dev/dl/"
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

        if debug:
            cmd.append("--debug")

        # Launch the Bubble Tea UI with stdout/stderr isolation
        sink_to_devnull = os.getenv("OMNI_TUI_SILENT", "0").lower() in {
            "1",
            "true",
            "yes",
        }
        cache_dir = self._user_cache_dir() or Path.home()
        cache_dir.mkdir(parents=True, exist_ok=True)
        sink_path = os.devnull if sink_to_devnull else str(cache_dir / "tui-server.log")

        orig_out = None
        orig_err = None
        sink_file = None
        exit_code = 0
        tried_rebuild = False
        try:
            orig_out_fd = os.dup(1)
            orig_err_fd = os.dup(2)
            sink_mode = "ab"
            sink_file = open(sink_path, sink_mode)
            os.dup2(sink_file.fileno(), 1)
            os.dup2(sink_file.fileno(), 2)

            orig_out = os.fdopen(orig_out_fd, "wb", closefd=False)
            orig_err = os.fdopen(orig_err_fd, "wb", closefd=False)

            while True:
                try:
                    proc = subprocess.Popen(
                        cmd, env=env, stdout=orig_out, stderr=orig_err
                    )
                    exit_code = proc.wait()
                    break
                except KeyboardInterrupt:
                    break
                except FileNotFoundError:
                    if not tried_rebuild:
                        binary_path = self._build_binary(debug=debug)
                        cmd[0] = str(binary_path)
                        tried_rebuild = True
                        continue
                    exit_code = 127
                    break
                except OSError as e:
                    if e.errno == errno.ENOEXEC and not tried_rebuild:
                        binary_path = self._build_binary(debug=debug)
                        cmd[0] = str(binary_path)
                        tried_rebuild = True
                        continue
                    exit_code = 1
                    break
        finally:
            try:
                if orig_out is not None:
                    orig_out.flush()
                if orig_err is not None:
                    orig_err.flush()
            except Exception:
                pass
            try:
                if orig_out is not None:
                    os.dup2(orig_out.fileno(), 1)
                if orig_err is not None:
                    os.dup2(orig_err.fileno(), 2)
            except Exception:
                pass
            try:
                if sink_file is not None:
                    sink_file.flush()
                    sink_file.close()
            except Exception:
                pass
            try:
                if orig_out is not None:
                    orig_out.close()
                if orig_err is not None:
                    orig_err.close()
            except Exception:
                pass

        if exit_code != 0 and exit_code != -2:
            print(f"Error: omni-tui exited with code {exit_code}")
            sys.exit(exit_code)

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

        vendor_binary = self._vendor_binary()
        if vendor_binary:
            return vendor_binary

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

    def _vendor_root(self) -> Path:
        return Path(__file__).parent / "vendor"

    def _vendor_binary(self) -> Optional[Path]:
        root = self._vendor_root()
        if not root.exists() or not root.is_dir():
            return None
        for target in self._target_triples():
            vendor_dir = root / target
            if not vendor_dir.exists() or not vendor_dir.is_dir():
                continue
            direct = vendor_dir / self._binary_filename()
            if direct.exists() and direct.is_file():
                self._ensure_executable(direct)
                return direct
            base_direct = vendor_dir / "omni-tui"
            if base_direct.exists() and base_direct.is_file():
                self._ensure_executable(base_direct)
                return base_direct
            archive = vendor_dir / "omni-tui.tar.gz"
            if archive.exists() and archive.is_file():
                extracted = self._extract_vendor_archive(archive, target)
                if extracted:
                    return extracted
            archive = vendor_dir / "omni-tui.zip"
            if archive.exists() and archive.is_file():
                extracted = self._extract_vendor_archive(archive, target)
                if extracted:
                    return extracted
        return None

    def _ensure_executable(self, path: Path) -> None:
        if platform.system().lower() != "windows":
            try:
                mode = path.stat().st_mode
                path.chmod(mode | 0o111)
            except Exception:
                pass

    def _vendor_cache_dir(self) -> Path:
        base = self._user_cache_dir()
        if base is None:
            base = Path.home() / ".omniagents"
        dest = base / "tui" / "vendor"
        dest.mkdir(parents=True, exist_ok=True)
        return dest

    def _extract_vendor_archive(self, archive: Path, target: str) -> Optional[Path]:
        cache_root = self._vendor_cache_dir() / target
        cache_root.mkdir(parents=True, exist_ok=True)
        try:
            digest = self._archive_digest(archive)
        except Exception:
            return None
        stamp = cache_root / "stamp"
        binary_name = self._binary_filename()
        if stamp.exists() and (cache_root / binary_name).exists():
            try:
                cached_digest = stamp.read_text(encoding="utf-8").strip()
            except Exception:
                cached_digest = ""
            if cached_digest and cached_digest == digest:
                candidate = cache_root / binary_name
                if candidate.exists():
                    self._ensure_executable(candidate)
                    return candidate
        for item in cache_root.iterdir():
            if item.is_file():
                item.unlink()
            elif item.is_dir():
                shutil.rmtree(item)
        if archive.name.endswith(".zip"):
            try:
                with zipfile.ZipFile(archive, "r") as zf:
                    zf.extractall(cache_root)
            except Exception:
                return None
        else:
            try:
                with tarfile.open(archive, "r:gz") as tf:
                    members = []
                    for member in tf.getmembers():
                        if member.isdev():
                            return None
                        members.append(member)
                    tf.extractall(cache_root, members=members)
            except Exception:
                return None
        candidate = self._locate_extracted_binary(cache_root, binary_name)
        if not candidate:
            return None
        self._ensure_executable(candidate)
        try:
            stamp.write_text(digest + "\n", encoding="utf-8")
        except Exception:
            pass
        return candidate

    def _locate_extracted_binary(self, root: Path, binary_name: str) -> Optional[Path]:
        matches = list(root.rglob(binary_name))
        if not matches:
            alt_name = "omni-tui.exe" if binary_name.endswith(".exe") else "omni-tui"
            matches = list(root.rglob(alt_name))
            if not matches:
                return None
        target_path = matches[0]
        dest_path = root / binary_name
        if target_path != dest_path:
            if dest_path.exists():
                dest_path.unlink()
            target_path.replace(dest_path)
            parent = target_path.parent
            while parent != root and parent.exists():
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent
            target_path = dest_path
        return target_path

    def _archive_digest(self, archive: Path) -> str:
        digest = hashlib.sha256()
        with open(archive, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        checksum_path = archive.with_name(archive.name + ".sha256")
        if checksum_path.exists() and checksum_path.is_file():
            try:
                expected = checksum_path.read_text(encoding="utf-8").strip().split()[0]
                if expected:
                    actual = digest.hexdigest()
                    if expected != actual:
                        raise ValueError("checksum mismatch for omni-tui archive")
            except Exception:
                pass
        return digest.hexdigest()

    def _target_triples(self) -> Iterable[str]:
        system = platform.system().lower()
        machine = platform.machine().lower()
        arch_map = {
            "x86_64": "x86_64",
            "amd64": "x86_64",
            "arm64": "aarch64",
            "aarch64": "aarch64",
        }
        arch = arch_map.get(machine, machine)
        mapping = {
            ("darwin", "x86_64"): ["x86_64-apple-darwin"],
            ("darwin", "aarch64"): ["aarch64-apple-darwin"],
            ("linux", "x86_64"): [
                "x86_64-unknown-linux-gnu",
                "x86_64-unknown-linux-musl",
            ],
            ("linux", "aarch64"): [
                "aarch64-unknown-linux-gnu",
                "aarch64-unknown-linux-musl",
            ],
            ("windows", "x86_64"): ["x86_64-pc-windows-msvc"],
            ("windows", "aarch64"): ["aarch64-pc-windows-msvc"],
        }
        return mapping.get((system, arch), [])

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
            print(
                "Error: Go toolchain not found. Install Go from https://go.dev/dl/ and retry."
            )
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
        build_cmd = ["go", "build", "-ldflags", "-s -w", "-o", str(out_path)]

        # Optional: ensure modules are fetched. go build will fetch as needed,
        # but we can explicitly run 'go mod download' to give clearer errors.
        try:
            subprocess.run(
                ["go", "mod", "download"],
                cwd=str(workdir),
                check=True,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except Exception:
            # Ignore; go build will try anyway and show errors if needed
            pass

        try:
            result = subprocess.run(build_cmd, cwd=str(workdir), env=env)
            if result.returncode != 0:
                print(
                    "Error: 'go build' failed. Ensure Go is installed and reachable, and you have internet to fetch modules."
                )
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


__all__ = ["BubbleTeaBackend"]
