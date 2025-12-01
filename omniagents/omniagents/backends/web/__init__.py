import os
import sys
import socket
import time
import platform
import shutil
import subprocess
import webbrowser
import logging
from pathlib import Path
from threading import Thread
from typing import Optional

from omniagents.core.agents.specs import AgentSpec
from omniagents.core.runtime.overrides import apply_cli_overrides
from omniagents.core.agents.specs import RealtimeSettings


class WebBackend:
    def run(self, spec: AgentSpec, **kwargs) -> None:
        try:
            from omniagents.backends.server.app import build_app
            import uvicorn
            from fastapi.staticfiles import StaticFiles
        except ImportError as e:
            print(
                "Error: Server dependencies not installed. Install with: pip install omniagents[server]"
            )
            print(f"Details: {e}")
            sys.exit(1)

        args = kwargs.get("args")
        host_arg = None
        port_arg = None
        try:
            host_arg = getattr(args, "host", None)
            port_arg = getattr(args, "port", None)
        except Exception:
            host_arg = None
            port_arg = None
        codespaces_env = any(
            os.environ.get(k)
            for k in (
                "CODESPACES",
                "CODESPACE_NAME",
                "GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN",
            )
        )
        host = host_arg or ("0.0.0.0" if codespaces_env else "127.0.0.1")
        if port_arg is not None:
            try:
                port = int(port_arg)
            except Exception:
                port = None
        else:
            port = None
        if port is None:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("", 0))
                port = s.getsockname()[1]

        apply_cli_overrides(spec, args)

        auto_open = self._should_auto_open(kwargs.get("auto_open"))
        minimal_ui = self._is_minimal_ui(kwargs.get("minimal_ui"))

        def _ensure_realtime_defaults(target_spec):
            try:
                if not getattr(target_spec, "realtime_mode", False):
                    target_spec.realtime_mode = True
                if not getattr(target_spec, "realtime_settings", None):
                    target_spec.realtime_settings = RealtimeSettings()
                rs = target_spec.realtime_settings
                if not rs.modalities or ("audio" not in rs.modalities):
                    rs.modalities = ["audio"]
                if not rs.model_name:
                    rs.model_name = "gpt-realtime"
                if not rs.voice:
                    rs.voice = "alloy"
                if not rs.input_audio_format:
                    rs.input_audio_format = "pcm16"
                if not rs.output_audio_format:
                    rs.output_audio_format = "pcm16"
                if not rs.turn_detection:
                    rs.turn_detection = {
                        "type": "server_vad",
                        "threshold": 0.3,
                        "prefix_padding_ms": 100,
                        "silence_duration_ms": 200,
                    }
                if rs.temperature is None:
                    rs.temperature = 0.8
                if rs.max_output_tokens is None:
                    rs.max_output_tokens = 4096
                if not rs.input_audio_transcription:
                    rs.input_audio_transcription = {"model": "whisper-1"}
            except Exception:
                pass

        realtime_spec = getattr(spec, "voice_spec", None) or spec
        _ensure_realtime_defaults(realtime_spec)

        config_path = kwargs.get("config_path")
        auth_token = kwargs.get("auth_token")
        debug = kwargs.get("debug", False)
        if not debug and args and hasattr(args, "debug"):
            debug = args.debug
        try:
            if debug:
                os.environ["OMNIAGENTS_SERVER_DEBUG"] = "1"
        except Exception:
            pass

        app = build_app(config_path=config_path, spec=spec, auth_token=auth_token)

        dev_flag_env = os.environ.get("OMNI_WEB_DEV", "0").lower() not in {
            "0",
            "false",
            "no",
        }
        dev_flag = bool(getattr(args, "dev", False)) or dev_flag_env
        dev_host = getattr(args, "dev_host", None) or host
        dev_port = int(getattr(args, "dev_port", 5173) or 5173)

        if not dev_flag:
            dist_dir = self._resolve_dist_dir(debug=debug)
            if dist_dir is None:
                print("Error: Web UI not found.")
                print("Manual build:")
                print("  cd omniagents/backends/web/ui")
                print("  npm install")
                print("  npm run build")
                sys.exit(1)
            app.mount(
                "/", StaticFiles(directory=str(dist_dir), html=True), name="webui"
            )

        def run_server():
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
                host=host,
                port=port,
                log_level="error",
                access_log=False,
                proxy_headers=True,
                forwarded_allow_ips="*",
            )

        server_thread = Thread(target=run_server, daemon=True)
        server_thread.start()

        max_retries = 10
        for i in range(max_retries):
            time.sleep(0.5)
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.settimeout(1)
                    s.connect(("127.0.0.1", port))
                    s.close()
                    break
            except (socket.timeout, ConnectionRefusedError):
                if i == max_retries - 1:
                    print(f"Error: Failed to start server on port {port}")
                    sys.exit(1)

        # Build URL with optional query params
        qp = []
        if auth_token:
            qp.append(("token", auth_token))
        try:
            if kwargs.get("resume"):
                qp.append(("resume", "true"))
            sid = kwargs.get("session_id")
            if sid:
                qp.append(("session", str(sid)))
            init = kwargs.get("initial_prompt")
            if init:
                qp.append(("initial", str(init)))
            if debug:
                qp.append(("debug", "1"))
            if minimal_ui:
                qp.append(("minimal", "true"))
        except Exception:
            pass
        open_url = None
        if dev_flag:
            try:
                open_url = self._start_dev_server(
                    ws_host=host, ws_port=port, dev_host=dev_host, dev_port=dev_port
                )
            except Exception as e:
                print(f"Error: Failed to start dev server: {e}")
                open_url = None
        if open_url is None:
            base = f"http://{host}:{port}/"
            if qp:
                from urllib.parse import urlencode

                qp.append(("ts", str(int(time.time()))))
                open_url = base + "?" + urlencode(qp)
            else:
                open_url = base + f"?ts={int(time.time())}"
        elif dev_flag and minimal_ui and open_url:
            if "minimal=" not in open_url:
                open_url = self._append_query_param(open_url, "minimal", "true")
        if auto_open:
            try:
                webbrowser.open(open_url)
            except Exception:
                print(f"Open in browser: {open_url}")
        else:
            print(f"Open in browser: {open_url}")

        try:
            while server_thread.is_alive():
                time.sleep(0.2)
        except KeyboardInterrupt:
            pass

    @staticmethod
    def _should_auto_open(auto_open_arg: bool | None) -> bool:
        env_val = os.environ.get("OMNI_WEB_AUTO_OPEN")
        env_enabled = (
            True
            if env_val is None
            else env_val.lower()
            not in {
                "0",
                "false",
                "no",
            }
        )
        if auto_open_arg is None:
            return env_enabled
        return bool(auto_open_arg)

    @staticmethod
    def _is_minimal_ui(minimal_arg: bool | None) -> bool:
        env_val = os.environ.get("OMNI_WEB_MINIMAL")
        env_enabled = env_val is not None and env_val.lower() not in {
            "0",
            "false",
            "no",
        }
        if minimal_arg is None:
            return env_enabled
        return bool(minimal_arg)

    @staticmethod
    def _append_query_param(url: str, key: str, value: str) -> str:
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}{key}={value}"

    def _start_dev_server(
        self, *, ws_host: str, ws_port: int, dev_host: str, dev_port: int
    ) -> Optional[str]:
        ui_dir = Path(__file__).parent / "ui"
        if not ui_dir.exists():
            return None
        if shutil.which("npm") is None:
            return None
        env = os.environ.copy()
        env["OMNI_WS_HOST"] = str(ws_host)
        env["OMNI_WS_PORT"] = str(ws_port)
        # Start Vite dev server
        cmd = [
            "npm",
            "run",
            "dev",
            "--",
            "--host",
            str(dev_host),
            "--port",
            str(dev_port),
        ]
        proc = subprocess.Popen(
            cmd,
            cwd=str(ui_dir),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        # Poll for readiness
        url = f"http://{dev_host}:{dev_port}/"
        for _ in range(40):
            time.sleep(0.5)
            try:
                import urllib.request

                with urllib.request.urlopen(url, timeout=1) as resp:
                    if resp.status == 200:
                        return url
            except Exception:
                continue
        return url

    def _resolve_dist_dir(self, debug: bool = False) -> Optional[Path]:
        override = os.environ.get("OMNI_WEB_UI_PATH")
        if override:
            p = Path(override).expanduser()
            if p.is_dir() and (p / "index.html").exists():
                return p
        packaged = Path(__file__).parent / "ui" / "dist"
        if packaged.is_dir() and (packaged / "index.html").exists():
            return packaged
        cache_dir = self._cache_dir()
        if cache_dir is not None:
            candidate = cache_dir / "dist"
            if candidate.is_dir() and (candidate / "index.html").exists():
                return candidate
        auto_build = os.environ.get("OMNI_WEB_AUTO_BUILD", "1").lower() not in {
            "0",
            "false",
            "no",
        }
        if auto_build:
            try:
                built = self._build_ui(debug=debug)
                return built
            except SystemExit:
                raise
            except Exception as e:
                print(f"Error: Failed to build web UI: {e}")
                return None
        return None

    def _build_ui(self, debug: bool = False) -> Path:
        if shutil.which("npm") is None or shutil.which("node") is None:
            print("Error: Node.js/npm not found.")
            raise SystemExit(1)
        try:
            v = subprocess.check_output(["node", "-v"], text=True).strip()
            if v.startswith("v"):
                v = v[1:]
            major = int((v.split(".") or ["0"])[0])
            if major < 18:
                print("Error: Node.js >= 18 required.")
                raise SystemExit(1)
        except Exception:
            pass

        src_base = Path(__file__).parent / "ui"
        if not src_base.exists():
            raise SystemExit(1)
        cache_dir = self._cache_dir() or src_base
        src_hash = self._compute_source_hash(src_base)
        stamp = self._stamp_path(cache_dir)
        need_sync = True
        try:
            if (
                stamp.exists()
                and src_hash
                and stamp.read_text(encoding="utf-8").strip() == src_hash
            ):
                need_sync = False
        except Exception:
            need_sync = True
        if need_sync and cache_dir != src_base:
            if cache_dir.exists():
                try:
                    shutil.rmtree(cache_dir)
                except Exception:
                    pass
            shutil.copytree(src_base, cache_dir)

        env = os.environ.copy()
        env.setdefault("NPM_CONFIG_AUDIT", "false")
        env.setdefault("NPM_CONFIG_FUND", "false")
        install_cmd = ["npm", "install"]
        lockfile = cache_dir / "package-lock.json"
        if lockfile.exists():
            install_cmd = ["npm", "ci"]
        try:
            subprocess.run(
                install_cmd,
                cwd=str(cache_dir),
                check=True,
                env=env,
                stdout=None if debug else subprocess.PIPE,
                stderr=None if debug else subprocess.PIPE,
                text=True,
            )
        except subprocess.CalledProcessError as e:
            raise SystemExit(e.returncode)
        try:
            subprocess.run(
                ["npm", "run", "build"],
                cwd=str(cache_dir),
                check=True,
                env=env,
                stdout=None if debug else subprocess.PIPE,
                stderr=None if debug else subprocess.PIPE,
                text=True,
            )
        except subprocess.CalledProcessError as e:
            raise SystemExit(e.returncode)
        out_dir = cache_dir / "dist"
        if not out_dir.is_dir() or not (out_dir / "index.html").exists():
            raise SystemExit(1)
        try:
            if src_hash:
                stamp.write_text(src_hash + "\n", encoding="utf-8")
        except Exception:
            pass
        return out_dir

    def _cache_dir(self) -> Optional[Path]:
        base = self._user_cache_dir()
        if base is None:
            return None
        dest = base / "web" / "ui"
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
        return cache_dir / "omni-web-ui.hash"

    def _compute_source_hash(self, src_base: Path) -> Optional[str]:
        try:
            files = []
            for root, _dirs, names in os.walk(src_base / "src"):
                for n in names:
                    if n.endswith(".ts") or n.endswith(".tsx"):
                        files.append(str(Path(root) / n))
            files.append(str(src_base / "package.json"))
            files.append(str(src_base / "tsconfig.json"))
            tcfg = src_base / "tailwind.config.js"
            if tcfg.exists():
                files.append(str(tcfg))
            pcfg = src_base / "postcss.config.js"
            if pcfg.exists():
                files.append(str(pcfg))
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


__all__ = ["WebBackend"]
