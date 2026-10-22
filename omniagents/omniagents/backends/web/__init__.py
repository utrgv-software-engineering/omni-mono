import os
import sys
import time
import platform
import shutil
import subprocess
import webbrowser
import logging
from pathlib import Path
from typing import Optional

from omniagents.core.agents.specs import AgentSpec
from omniagents.core.runtime.overrides import apply_cli_overrides
from omniagents.core.agents.specs import RealtimeSettings


class WebBackend:
    def run(self, spec: AgentSpec, **kwargs) -> None:
        try:
            from omniagents.backends.server.app import build_app
            from omniagents.core.utils.serving import (
                BackgroundServer,
                bind_first_free,
                bind_socket,
            )
            import uvicorn
            from fastapi.staticfiles import StaticFiles
        except ImportError as e:
            print(
                "Error: Server dependencies not installed. Install with: pip install omniagents[server]"
            )
            print(f"Details: {e}")
            sys.exit(1)

        args = kwargs.get("args")
        codespaces_env = any(
            os.environ.get(k)
            for k in (
                "CODESPACES",
                "CODESPACE_NAME",
                "GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN",
            )
        )

        # Check for explicit CLI overrides
        host_arg = getattr(args, "host", None) if args else None
        port_arg = getattr(args, "port", None) if args else None
        ssl_cert = getattr(args, "ssl_cert", None) if args else None
        ssl_key = getattr(args, "ssl_key", None) if args else None

        # Default: localhost:8080 with sequential fallback (or 0.0.0.0 in Codespaces)
        if host_arg:
            host = host_arg
        else:
            host = "0.0.0.0" if codespaces_env else "127.0.0.1"
        # Bind now and keep the socket for uvicorn: the port printed and
        # opened below is then the one the server listens on.
        try:
            if port_arg is not None:
                sock = bind_socket(host, int(port_arg))
            else:
                sock = bind_first_free(host, 8080)
        except (OSError, RuntimeError) as e:
            print(f"Error: Failed to bind {host}: {e}")
            sys.exit(1)
        port = sock.getsockname()[1]

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
        voice_backend = getattr(realtime_spec, "voice_backend", "realtime")
        if voice_backend == "realtime":
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

        # Web auto-launches the server in the user's terminal too — same
        # contract as ink. The user's cwd is the implicit workspace_root;
        # the in-browser workspace picker can override it per-session via
        # session.ensure variables (App.tsx). Production server deploys
        # (omni --mode server) leave this unset so missing values raise
        # rather than silently anchor to the server's cwd.
        app = build_app(
            config_path=config_path,
            spec=spec,
            auth_token=auth_token,
            default_host_workspace_root=os.getcwd(),
        )

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
            # SPA shell with pre-bootstrap workspace seed. We intercept GET /
            # and GET /index.html before the StaticFiles mount so we can inject
            # ``window.__OMNI_BOOT__`` carrying the workspace_root. The SPA
            # reads it for the initial workspace-pill state — otherwise the
            # pill flashes "Select workspace" for the entire boot window
            # while ``fs_get_workspace_root`` round-trips. The asset routes
            # (everything except the two HTML paths) still go through the
            # StaticFiles mount registered below.
            self._register_spa_shell_route(app, dist_dir, workspace_root=os.getcwd())
            app.mount(
                "/", StaticFiles(directory=str(dist_dir), html=True), name="webui"
            )

        # Determine if SSL is enabled
        use_ssl = bool(ssl_cert and ssl_key)

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
        config_kwargs = {
            "log_level": "error",
            "access_log": False,
            "proxy_headers": True,
            "forwarded_allow_ips": "*",
        }
        if use_ssl:
            config_kwargs["ssl_certfile"] = ssl_cert
            config_kwargs["ssl_keyfile"] = ssl_key
        try:
            server = BackgroundServer(uvicorn.Config(app, **config_kwargs), sock).start()
        except RuntimeError as e:
            print(f"Error: Failed to start server on port {port}: {e}")
            sys.exit(1)
        server_thread = server.thread

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
            # Use 'localhost' instead of '127.0.0.1' for browser URL
            # Chrome requires this for secure context (getUserMedia, etc.)
            url_host = (
                "localhost" if host in {"127.0.0.1", "0.0.0.0", "::", "::1"} else host
            )
            scheme = "https" if use_ssl else "http"
            base = f"{scheme}://{url_host}:{port}/"
            if qp:
                from urllib.parse import urlencode

                qp.append(("ts", str(int(time.time()))))
                open_url = base + "?" + urlencode(qp)
            else:
                open_url = base + f"?ts={int(time.time())}"
        elif dev_flag and minimal_ui and open_url:
            if "minimal=" not in open_url:
                open_url = self._append_query_param(open_url, "minimal", "true")
        output_format = getattr(args, "output", "text") if args else "text"
        if output_format == "json":
            import json

            print(json.dumps({"url": open_url, "port": port}), flush=True)
        elif auto_open:
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
    def _register_spa_shell_route(app, dist_dir: Path, *, workspace_root: str) -> None:
        """Serve ``index.html`` with an injected ``window.__OMNI_BOOT__`` blob
        so the SPA can seed boot-dependent UI (today: the workspace pill)
        before the first server round-trip resolves.

        Mounted on ``GET /`` and ``GET /index.html``. Registered *before* the
        catch-all StaticFiles mount in the caller; FastAPI's route matcher
        prefers the explicit routes, so only the HTML shell is rewritten —
        all other asset paths fall through to StaticFiles unmodified.
        """
        import json as _json
        from fastapi.responses import HTMLResponse

        index_path = dist_dir / "index.html"

        def _build_payload() -> bytes:
            html = index_path.read_text(encoding="utf-8")
            blob = _json.dumps({"workspace_root": workspace_root})
            # Inline script that runs before any module script — ``<script>``
            # tags execute in document order, and the inlined assignment
            # synchronously beats the deferred Vite module bundle. We inject
            # right before ``</head>`` so the global is set during HTML
            # parsing, well before React mounts.
            inject = f"<script>window.__OMNI_BOOT__ = {blob};</script>"
            if "</head>" in html:
                return html.replace("</head>", inject + "</head>", 1).encode("utf-8")
            # Fallback: no </head> (unexpected Vite output) — prepend instead
            # of failing, the global still lands before <body>.
            return (inject + html).encode("utf-8")

        async def _serve_shell():
            return HTMLResponse(_build_payload())

        app.add_api_route("/", _serve_shell, methods=["GET"], include_in_schema=False)
        app.add_api_route(
            "/index.html",
            _serve_shell,
            methods=["GET"],
            include_in_schema=False,
        )

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
