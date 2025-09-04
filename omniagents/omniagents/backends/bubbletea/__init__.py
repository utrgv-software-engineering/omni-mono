"""Bubble Tea TUI backend for OmniAgents."""

import os
import sys
import subprocess
import socket
import time
from pathlib import Path
from threading import Thread
from typing import Optional

from omniagents.core.agents.specs import AgentSpec


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
        
        # Find the Go binary
        binary_path = self._find_binary()
        if not binary_path:
            print("Error: omni-tui binary not found.")
            print("\nTo build the binary, run:")
            print("  cd omniagents/backends/bubbletea/cmd/omni-tui")
            print("  go build -o ../../bin/omni-tui")
            print("\nOr install Go dependencies first:")
            print("  go mod download")
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
        try:
            # Run the Go binary and wait for it to complete
            result = subprocess.run(cmd, env=env)
            
            # Check if the process exited with an error
            if result.returncode != 0 and result.returncode != -2:  # -2 is Ctrl+C
                print(f"Error: omni-tui exited with code {result.returncode}")
                sys.exit(result.returncode)
                
        except KeyboardInterrupt:
            # User pressed Ctrl+C, exit cleanly
            pass
        except FileNotFoundError:
            print(f"Error: Binary not found at {binary_path}")
            print("Please build the Go binary first (see instructions above)")
            sys.exit(1)
        except Exception as e:
            print(f"Error launching TUI: {e}")
            sys.exit(1)
    
    def _find_binary(self) -> Optional[Path]:
        """Find the omni-tui binary.
        
        Searches in the following locations:
        1. Compiled binary in bin/ directory
        2. Development binary in cmd/omni-tui/ directory
        3. System PATH
        
        Returns:
            Path to the binary if found, None otherwise
        """
        # Base directory (where this file is located)
        base = Path(__file__).parent
        
        # Check bin directory first (preferred location)
        bin_binary = base / "bin" / "omni-tui"
        if bin_binary.exists() and bin_binary.is_file():
            return bin_binary
        
        # Check development location (where go build would put it)
        dev_binary = base / "cmd" / "omni-tui" / "omni-tui"
        if dev_binary.exists() and dev_binary.is_file():
            return dev_binary
        
        # Check if it's in system PATH
        import shutil
        system_binary = shutil.which("omni-tui")
        if system_binary:
            return Path(system_binary)
        
        return None


__all__ = ['BubbleTeaBackend']