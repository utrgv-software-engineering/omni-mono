"""Voice backend for OmniAgents using OpenAI Realtime API.

This backend provides push-to-talk voice interaction with AI agents using
OpenAI's Realtime API. It requires:
- PyAudio for audio I/O
- pynput for keyboard input (push-to-talk with SPACEBAR)
- An agent configured with realtime_mode: true

Environment variables:
- OMNI_DEBUG: if set, enables additional debug logging.
"""

import os
import sys
import subprocess
import socket
import time
from pathlib import Path
import platform
import shutil
from threading import Thread
from typing import Optional
import logging

from omniagents.core.agents.specs import AgentSpec
from omniagents.core.runtime.overrides import apply_cli_overrides


class VoiceBackend:
    """Voice backend implementation using OpenAI Realtime API.

    This backend launches a voice client that communicates with
    the Python agent server via WebSocket/JSON-RPC protocol.
    Push-to-talk interaction with SPACEBAR.
    """

    def run(self, spec: AgentSpec, **kwargs) -> None:
        """Run the Voice backend with the given agent specification.

        Args:
            spec: The agent specification containing configuration,
                  instructions, tools, and other settings
            **kwargs: Additional backend-specific arguments including:
                - config_path: Path to the YAML configuration file
                - auth_token: Optional authentication token
                - debug: Enable debug mode
                - session_id: Resume a specific session
        """
        # Validate realtime mode
        if not spec.realtime_mode:
            print(
                "Error: Voice backend requires realtime_mode: true in agent configuration"
            )
            print("Please add 'realtime_mode: true' to your agent.yaml file")
            sys.exit(1)

        # Check for audio modality
        if spec.realtime_settings:
            modalities = spec.realtime_settings.modalities or []
            if "audio" not in modalities:
                print("Error: Voice backend requires audio modality")
                print(
                    "Please ensure realtime_settings.modalities includes 'audio' in your agent.yaml"
                )
                sys.exit(1)
        else:
            print("Error: Voice backend requires realtime_settings configuration")
            print("Please add realtime_settings to your agent.yaml")
            sys.exit(1)

        # Import server dependencies
        try:
            from omniagents.backends.server.app import build_app
            import uvicorn
        except ImportError as e:
            print("Error: Server dependencies not installed.")
            print("Install with: pip install omniagents[server]")
            print(f"Details: {e}")
            sys.exit(1)

        # Check for voice client dependencies
        try:
            import pyaudio
        except ImportError:
            print("Error: PyAudio not installed")
            print("Install with: pip install omniagents[voice]")
            print("\nOn macOS, you may need: brew install portaudio")
            print("On Linux: sudo apt-get install portaudio19-dev")
            sys.exit(1)

        try:
            from pynput import keyboard
        except ImportError:
            print("Error: pynput not installed")
            print("Install with: pip install omniagents[voice]")
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
        debug = kwargs.get("debug", False)
        session_id = kwargs.get("session_id")

        # Build the FastAPI app with the agent spec
        app = build_app(config_path=config_path, spec=spec, auth_token=auth_token)

        # Start server in background thread
        def run_server():
            """Run the FastAPI server in a background thread."""
            # Minimal safeguard: silence noisy third-party loggers so their
            # output cannot corrupt the voice client output.
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
                log_level="error",  # Suppress server logs
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

        # Build server URL - use /ws/realtime endpoint for realtime mode
        server_url = f"ws://127.0.0.1:{port}/ws/realtime"

        # Debug output
        if debug:
            print(f"[DEBUG] Server URL: {server_url}")
            print(f"[DEBUG] Agent name: {spec.name}")

        # Launch voice client
        try:
            from omniagents.backends.voice.client import run_voice_client

            run_voice_client(
                server_url=server_url,
                agent_name=spec.name or "Voice Agent",
                auth_token=auth_token,
                session_id=session_id,
                debug=debug,
            )
        except KeyboardInterrupt:
            pass
        except Exception as e:
            print(f"Error: Voice client failed: {e}")
            if debug:
                import traceback

                traceback.print_exc()
            sys.exit(1)


__all__ = ["VoiceBackend"]
