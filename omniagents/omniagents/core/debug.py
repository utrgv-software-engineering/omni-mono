"""Unified debug configuration for OmniAgents."""

import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


class _DebugFileHandler(logging.Handler):
    """Logging handler that writes to the Debug log file."""

    def __init__(self, log_file):
        super().__init__()
        self._log_file = log_file
        self.setFormatter(
            logging.Formatter(
                "[%(asctime)s] [%(name)s] %(levelname)s: %(message)s",
                datefmt="%H:%M:%S",
            )
        )

    def emit(self, record):
        try:
            msg = self.format(record)
            self._log_file.write(msg + "\n")
            self._log_file.flush()
        except Exception:
            pass


class Debug:
    """Simple unified debug state for OmniAgents."""

    _enabled: bool = False
    _log_file = None
    _stdout_redirected: bool = False
    _original_stdout = None
    _stderr_redirected: bool = False
    _original_stderr = None

    @classmethod
    def init(cls, cli_flag: bool = False, yaml_config: bool = False):
        """Initialize debug state once at startup.

        Priority: CLI flag > YAML config > Environment variable

        Args:
            cli_flag: Debug flag from command line arguments
            yaml_config: Debug flag from YAML configuration
        """
        # Priority: CLI > YAML > Environment
        cls._enabled = (
            cli_flag
            or yaml_config
            or os.getenv("OMNIAGENTS_DEBUG", "").lower() in ("1", "true")
        )

        # If enabled via CLI, set env var for child processes
        if cli_flag and cls._enabled:
            os.environ["OMNIAGENTS_DEBUG"] = "1"

        # Open log file when debug is enabled
        if cls._enabled:
            log_path = Path.home() / ".omniagents-debug.log"
            cls._log_file = open(log_path, "a")
            cls.log(f"=== Debug session started (pid={os.getpid()}) ===")

            # Route all Python logging to the debug log file so that
            # logger.error/warning/debug/info calls are never lost,
            # even when stdout is redirected or loggers are silenced
            # by the backend.
            handler = _DebugFileHandler(cls._log_file)
            handler.setLevel(logging.DEBUG)
            root = logging.getLogger()
            root.addHandler(handler)
            root.setLevel(logging.DEBUG)

    @classmethod
    def capture_stdout(cls):
        """Redirect sys.stdout to the debug log file (or /dev/null).

        Call this before launching a TUI backend so that stray print()
        calls never corrupt the terminal.  When debug is enabled the
        output lands in the log file; otherwise it is discarded.

        The real stdout is preserved in _original_stdout so that
        subprocesses (e.g. the Ink TUI) can still use the terminal.
        """
        if cls._stdout_redirected:
            return
        cls._original_stdout = sys.stdout
        if cls._enabled and cls._log_file:
            sys.stdout = cls._log_file
        else:
            sys.stdout = open(os.devnull, "w")
        cls._stdout_redirected = True

    @classmethod
    def original_stdout(cls):
        """Return the real stdout (pre-redirect) for subprocess I/O."""
        return cls._original_stdout or sys.__stdout__

    @classmethod
    def capture_stderr(cls):
        """Redirect sys.stderr to the debug log file (or /dev/null).

        Python logging's default handlers write to stderr — one
        ``logger.error(..., exc_info=True)`` from a run error is a
        hundred raw traceback lines straight into the terminal a TUI
        backend is rendering on. Call this beside ``capture_stdout``
        before launching a TUI so the frame can never be corrupted;
        with debug enabled the output lands in the log file, otherwise
        it is discarded.

        Handlers that captured the old stderr at construction time are
        retargeted so pre-existing logging configs follow the redirect.
        """
        if cls._stderr_redirected:
            return
        cls._original_stderr = sys.stderr
        target = (
            cls._log_file
            if (cls._enabled and cls._log_file)
            else open(os.devnull, "w")
        )
        old = sys.stderr
        sys.stderr = target
        for handler in logging.getLogger().handlers:
            if getattr(handler, "stream", None) is old:
                handler.stream = target
        cls._stderr_redirected = True

    @classmethod
    def original_stderr(cls):
        """Return the real stderr (pre-redirect) for subprocess I/O."""
        return cls._original_stderr or sys.__stderr__

    @classmethod
    def enabled(cls) -> bool:
        """Check if debug is enabled."""
        return cls._enabled

    @classmethod
    def log(cls, msg: str):
        """Write a timestamped debug line to the log file.

        Falls back to stderr if the log file isn't open.
        """
        if not cls._enabled:
            return
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]
        line = f"[{ts}] {msg}\n"
        if cls._log_file:
            cls._log_file.write(line)
            cls._log_file.flush()
        else:
            sys.stderr.write(line)

    @classmethod
    def set_enabled(cls, enabled: bool):
        """Manually set debug state (useful for testing)."""
        cls._enabled = enabled
        if enabled:
            os.environ["OMNIAGENTS_DEBUG"] = "1"
        else:
            os.environ.pop("OMNIAGENTS_DEBUG", None)
