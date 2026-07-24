"""Shell execution tools: foreground ``execute_bash`` plus background jobs.

``execute_bash`` runs a command in the active workspace (host or sandbox).
With ``background=True`` it spawns the command detached instead, streaming
stdout+stderr to ``workspace_root/.omniagents/jobs/{job_id}.log`` and
returning immediately with the job handle. Three companion tools manage
backgrounded jobs:

  * ``bash_status`` — poll a job (or list all known jobs).
  * ``bash_logs``   — read the tail (or head) of a job's log file.
  * ``bash_kill``   — terminate a job (SIGTERM, then SIGKILL after grace).

Job state lives in the process-wide registry in ``_bash_jobs``.
"""

from __future__ import annotations

import os
import shlex
import time
from typing import Any, Dict, Optional

from agents.run_context import RunContextWrapper

from omniagents.core.tools import (
    RichToolOutput,
    approval_metadata,
    rich_function_tool,
    session_id_from_ctx,
)

from ._bash_jobs import (
    Job,
    _watch_job,
    get_registry,
    job_state_dict,
    jobs_snapshot,
    log_path_for,
    new_job_id,
    read_job_log,
    terminate_job,
)
from ._workspace import (
    require_workspace_root,
    shell_unavailable_payload,
    workspace_root_from_ctx,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BASH_DEFAULT_TIMEOUT = 30
_BASH_DEFAULT_MAX_OUTPUT_CHARS = 40000  # ~40KB
_BASH_MAX_LINE_LENGTH = 500

_LOGS_DEFAULT_LINES = 200
_LOGS_DEFAULT_MAX_CHARS = 40000

_KILL_DEFAULT_GRACE_SECONDS = 5


# Side-channel snapshot ride-along — the helper lives in _bash_jobs.py so
# server functions can build the same shape. Tool call sites use
# ``_snapshot_for(ctx)`` so the snapshot stamped on tool_result events is
# filtered to the calling session — otherwise session A's tool result
# would leak session B's jobs to A's UI.
_jobs_snapshot = jobs_snapshot


def _session_id_from_ctx(ctx: RunContextWrapper[Any]) -> Optional[str]:
    """Jobs without a session become "uncategorized" (visible to all
    sessions; see ``_job_visible_to``)."""
    return session_id_from_ctx(ctx)


def _snapshot_for(ctx: RunContextWrapper[Any]) -> list[Dict[str, Any]]:
    return jobs_snapshot(session_id=_session_id_from_ctx(ctx))


# ---------------------------------------------------------------------------
# Output shaping helpers
# ---------------------------------------------------------------------------


def _command_summary(exit_code: int, wall_time_ms: int, truncated: bool) -> str:
    parts = [f"exit {exit_code}", f"{wall_time_ms}ms"]
    if truncated:
        parts.append("truncated")
    return f"Ran command ({', '.join(parts)})"


def _truncate_output_head_tail(text: str, max_chars: int) -> tuple[str, int, bool]:
    """Truncate text preserving head and tail, dropping the middle.

    Returns (truncated_text, chars_removed, was_truncated).
    """
    if len(text) <= max_chars:
        return text, 0, False
    if max_chars <= 0:
        return "", len(text), True
    head_budget = max_chars // 2
    tail_budget = max_chars - head_budget
    head = text[:head_budget]
    tail = text[-tail_budget:] if tail_budget > 0 else ""
    chars_removed = len(text) - len(head) - len(tail)
    truncated = f"{head}\n\n[...{chars_removed:,} characters truncated...]\n\n{tail}"
    return truncated, chars_removed, True


def _truncate_long_lines(
    text: str, max_line_length: int = _BASH_MAX_LINE_LENGTH
) -> tuple[str, int]:
    lines = text.split("\n")
    result_lines = []
    truncated_count = 0
    for line in lines:
        if len(line) > max_line_length:
            result_lines.append(line[:max_line_length] + "...")
            truncated_count += 1
        else:
            result_lines.append(line)
    return "\n".join(result_lines), truncated_count


def _resolve_cwd(ctx: RunContextWrapper[Any], cwd: Optional[str], where: str) -> str:
    """Resolve cwd against workspace_root: absolute paths are honored
    as-is; relative or empty values are anchored to the workspace."""
    cwd_is_absolute = isinstance(cwd, str) and cwd.strip() and os.path.isabs(cwd)
    if cwd_is_absolute:
        workspace_root_from_ctx(ctx)  # informational only
        return cwd  # type: ignore[return-value]
    wr = require_workspace_root(ctx, where=where)
    if cwd is None or (isinstance(cwd, str) and not cwd.strip()):
        return wr
    return os.path.abspath(os.path.join(wr, cwd))


# ---------------------------------------------------------------------------
# execute_bash — foreground (synchronous) + background modes
# ---------------------------------------------------------------------------


class _BashTimeoutMarker(Exception):
    """Internal sentinel used to route a workspace.exec timeout into the
    same error-handling branch as ``subprocess.TimeoutExpired``. The
    workspace layer flattens timeouts into ``ExecResult(exit_code=-1)``
    instead of raising; we re-raise this marker so the existing
    structured error output is preserved without rewriting the tool's
    exception flow.
    """

    def __init__(self, timeout: int, wall_time_ms: int) -> None:
        self.timeout = timeout
        self.wall_time_ms = wall_time_ms


@rich_function_tool(running_summary="Running shell command...")
async def execute_bash(
    ctx: RunContextWrapper[Any],
    command: str,
    timeout: Optional[int] = None,
    cwd: Optional[str] = None,
    max_output_chars: Optional[int] = None,
    background: bool = False,
) -> RichToolOutput:
    """
    Executes a shell command in the user's environment and returns the output.

    Args:
        command: The shell command to execute.
        timeout: Maximum execution time in seconds (default: 30). Ignored when
            ``background=True``.
        cwd: Working directory for the command. Absolute paths are honored as-is;
            relative or empty values are anchored to ``workspace_root``.
        max_output_chars: Maximum characters to return (default: 40000). If the
            output exceeds this, the middle is truncated while preserving the
            beginning and end. Ignored when ``background=True``.
        background: If ``True``, spawn the command detached, stream stdout and
            stderr to a per-job log file under
            ``workspace_root/.omniagents/jobs/<job_id>.log``, and return
            immediately with the job handle (``job_id``, ``pid``, ``log_path``).

            When the job exits, an automatic notification is delivered to
            this session — the next turn starts with the exit code and
            timing in context. You can end the current turn after
            spawning; the completion will wake you. Use ``bash_status`` /
            ``bash_logs`` only if you need to inspect mid-execution
            within the same turn. Use ``bash_kill`` to terminate early.

    Returns:
        RichToolOutput. In foreground mode the metadata mirrors the synchronous
        bash tool (stdout, stderr, exit_code, timing). In background mode the
        metadata contains ``job_id``, ``pid``, ``log_path``, and ``cwd``; the
        process keeps running after this call returns.

    Examples:
        # Foreground (default) — blocks until the command finishes:
        execute_bash("ls -la")

        # Background — returns immediately with a job_id:
        execute_bash("npm run dev", background=True)
    """
    from omniagents import workspace as _ws

    # Short-circuit for shell-less backends (e.g. PyodideWorkspace).
    # Applies to both foreground and background — neither can work without
    # the active workspace exposing a real shell.
    if not _ws.current_workspace().supports_shell:
        llm_output, ui_metadata = shell_unavailable_payload(
            tool="execute_bash", command=command
        )
        return RichToolOutput(llm_output, ui_metadata)

    if background:
        return await _execute_bash_background(ctx, command=command, cwd=cwd)

    if timeout is None:
        timeout = _BASH_DEFAULT_TIMEOUT
    if max_output_chars is None:
        max_output_chars = _BASH_DEFAULT_MAX_OUTPUT_CHARS

    cwd = _resolve_cwd(ctx, cwd, where="execute_bash")
    start_time = time.monotonic()

    try:
        # Route through the workspace exec primitive so the tool
        # transparently runs the command on the host or inside a
        # sandbox session.
        exec_result = await _ws.exec(
            command,
            cwd=cwd,
            timeout=float(timeout),
            shell=True,
        )

        wall_time_ms = int((time.monotonic() - start_time) * 1000)

        stdout_str = exec_result.stdout or ""
        stderr_str = exec_result.stderr or ""
        return_code = exec_result.exit_code

        # HostWorkspace.exec encodes timeouts as exit_code=-1 with a
        # "timed out" message embedded in stdout. Reproduce the previous
        # tool's TimeoutExpired branch so the error metadata matches
        # existing tests and downstream consumers.
        timed_out = return_code == -1 and (
            "timed out" in stdout_str.lower() or "timed out" in stderr_str.lower()
        )
        if timed_out:
            raise _BashTimeoutMarker(timeout=timeout, wall_time_ms=wall_time_ms)

        combined_output = stdout_str
        if stderr_str:
            combined_output = (
                combined_output
                + ("\n" if combined_output else "")
                + f"[stderr]\n{stderr_str}"
            )

        original_length = len(combined_output)
        total_lines = combined_output.count("\n") + 1 if combined_output else 0

        combined_output, lines_truncated = _truncate_long_lines(
            combined_output, _BASH_MAX_LINE_LENGTH
        )
        combined_output, chars_removed, was_truncated = _truncate_output_head_tail(
            combined_output, max_output_chars
        )

        header_lines = []
        if return_code == 0:
            header_lines.append(f"Exit code: 0 (success) | Wall time: {wall_time_ms}ms")
        else:
            header_lines.append(
                f"Exit code: {return_code} (failed) | Wall time: {wall_time_ms}ms"
            )

        if was_truncated or lines_truncated > 0:
            header_lines.append(
                f"Total output: {original_length:,} chars, {total_lines:,} lines"
            )
            if lines_truncated > 0:
                header_lines.append(
                    f"[{lines_truncated} lines exceeded {_BASH_MAX_LINE_LENGTH} chars and were truncated]"
                )
            if was_truncated:
                header_lines.append(
                    f"[Output truncated: {chars_removed:,} characters removed from middle]"
                )

        header = "\n".join(header_lines) + "\n\n" if header_lines else ""
        llm_output = header + (combined_output if combined_output else "(no output)")

        preview_lines = combined_output.split("\n")[:20] if combined_output else []
        preview = "\n".join(preview_lines)
        ui_truncated = (
            len(combined_output.split("\n")) > 20 if combined_output else False
        )

        ui_metadata = {
            "value": combined_output,
            "display_type": "command",
            "summary": _command_summary(return_code, wall_time_ms, was_truncated),
            "preview": preview,
            "truncated": ui_truncated or was_truncated,
            "metadata": {
                "command": command,
                "stdout": stdout_str,
                "stderr": stderr_str,
                "exit_code": return_code,
                "success": return_code == 0,
                "has_stderr": bool(stderr_str),
                "wall_time_ms": wall_time_ms,
                "original_chars": original_length,
                "returned_chars": len(combined_output),
                "chars_truncated": chars_removed,
                "lines_truncated": lines_truncated,
                "total_lines": total_lines,
                "was_truncated": was_truncated,
                "cwd": cwd,
                "background": False,
            },
        }
        return RichToolOutput(llm_output, ui_metadata)

    except _BashTimeoutMarker as marker:
        wall_time_ms = marker.wall_time_ms
        llm_output = (
            f"Command timed out after {timeout} seconds (wall time: {wall_time_ms}ms).\n"
            f"For long-running commands (dev servers, watchers, builds), call "
            f"execute_bash with background=True and manage the job via "
            f"bash_status, bash_logs, and bash_kill."
        )
        ui_metadata = {
            "value": f"Command timed out after {timeout} seconds",
            "display_type": "error",
            "summary": "Command timeout",
            "preview": f"Command '{command}' exceeded timeout of {timeout} seconds",
            "truncated": False,
            "metadata": {
                "error_type": "timeout",
                "command": command,
                "timeout": timeout,
                "wall_time_ms": wall_time_ms,
            },
        }
        return RichToolOutput(llm_output, ui_metadata)

    except Exception as e:
        wall_time_ms = int((time.monotonic() - start_time) * 1000)
        llm_output = f"Error executing command: {str(e)}"
        ui_metadata = {
            "value": f"Error executing command: {str(e)}",
            "display_type": "error",
            "summary": "Execution error",
            "preview": f"Failed to execute '{command}': {str(e)}",
            "truncated": False,
            "metadata": {
                "error_type": "execution_error",
                "command": command,
                "error": str(e),
                "wall_time_ms": wall_time_ms,
            },
        }
        return RichToolOutput(llm_output, ui_metadata)


async def _execute_bash_background(
    ctx: RunContextWrapper[Any],
    *,
    command: str,
    cwd: Optional[str],
) -> RichToolOutput:
    """Spawn a background bash job via the workspace abstraction.

    The shell command is wrapped with ``> log 2>&1`` redirection so the
    spawned shell writes its own log file (host or sandbox-side, depending
    on the active workspace). We then call ``workspace.spawn(pty=False)``
    which returns a ``ProcessHandle`` — host gives us a real
    ``asyncio.subprocess.Process``; sandbox gives us a
    ``pty_exec_start``-tracked handle. Either way the Job registry holds
    the handle uniformly.
    """
    import asyncio as _asyncio

    from omniagents import workspace as _ws

    # Background jobs require a real workspace_root for log placement; we
    # always anchor logs there even if the user supplied an absolute cwd.
    workspace_root = require_workspace_root(ctx, where="execute_bash(background=True)")
    resolved_cwd = _resolve_cwd(ctx, cwd, where="execute_bash(background=True)")
    session_id = _session_id_from_ctx(ctx)

    job_id = new_job_id()
    log_path = log_path_for(workspace_root, job_id)

    # Ensure the log file exists before we spawn so callers (and tests)
    # can stat it the instant ``execute_bash`` returns. Doing it via the
    # workspace keeps the operation sandbox-correct. ``mkdir -p`` + ``touch``
    # are both path-policy-safe (exec-based) in the sandbox backend.
    log_dir = os.path.dirname(log_path)
    try:
        await _ws.exec(
            f"mkdir -p {shlex.quote(log_dir)} && : > {shlex.quote(log_path)}",
            shell=True,
            timeout=5.0,
        )
    except Exception:
        pass  # best-effort; the redirection below will fail loudly otherwise

    started_at_wall = time.time()
    started_at_monotonic = time.monotonic()

    # Wrap the user's command with output redirection so the spawned shell
    # writes the log itself. This works identically on host and sandbox:
    # the shell process inside whichever backend redirects to a path it
    # can write to.
    wrapped_command = f"({command}) >> {shlex.quote(log_path)} 2>&1 < /dev/null"

    try:
        handle = await _ws.spawn(
            wrapped_command,
            cwd=resolved_cwd,
            shell=True,
            pty=False,
        )
    except Exception as e:
        # Surface spawn failure as an execution_error, mirroring the
        # foreground path's error envelope.
        return RichToolOutput(
            f"Error spawning background command: {str(e)}",
            {
                "value": f"Error spawning background command: {str(e)}",
                "display_type": "error",
                "summary": "Background spawn error",
                "preview": f"Failed to spawn '{command}': {str(e)}",
                "truncated": False,
                "bash_jobs_snapshot": _snapshot_for(ctx),
                "metadata": {
                    "error_type": "spawn_error",
                    "command": command,
                    "error": str(e),
                    "background": True,
                },
            },
        )

    job = Job(
        job_id=job_id,
        command=command,
        cwd=resolved_cwd,
        log_path=log_path,
        started_at_wall=started_at_wall,
        started_at_monotonic=started_at_monotonic,
        handle=handle,
        session_id=session_id,
    )
    # Kick off the watcher task that observes the handle for natural exit
    # and caches the final exit code on the Job. Held on the Job so the
    # registry can cancel it during cleanup.
    job.watcher_task = _asyncio.create_task(
        _watch_job(job), name=f"bash-job-watcher-{job_id}"
    )
    get_registry().register(job)

    pid_str = str(handle.pid) if handle.pid is not None else "n/a"
    llm_output = (
        f"Started background job {job_id} (pid {pid_str}).\n"
        f"Logs: {log_path}\n"
        f"A completion notification (with exit code and timing) will arrive "
        f"automatically when the job exits — you can end this turn after spawning.\n"
        f"Use bash_status('{job_id}') / bash_logs('{job_id}') only if you need "
        f"to peek mid-run, or bash_kill('{job_id}') to terminate early."
    )
    ui_metadata = {
        "value": llm_output,
        "display_type": "command",
        "summary": f"Started background job {job_id}",
        "preview": f"$ {command}\n→ pid {pid_str}, logs at {log_path}",
        "truncated": False,
        "bash_jobs_snapshot": _snapshot_for(ctx),
        "metadata": {
            "command": command,
            "background": True,
            "job_id": job_id,
            "pid": handle.pid,
            "log_path": log_path,
            "cwd": resolved_cwd,
            "started_at": started_at_wall,
        },
    }
    return RichToolOutput(llm_output, ui_metadata)


# ---------------------------------------------------------------------------
# bash_status — poll one or all background jobs
# ---------------------------------------------------------------------------


_job_state_dict = job_state_dict


@rich_function_tool(running_summary="Checking background job status...")
async def bash_status(
    ctx: RunContextWrapper[Any],
    job_id: Optional[str] = None,
    list_all: bool = False,
) -> RichToolOutput:
    """
    Reports the state of background bash jobs spawned via
    ``execute_bash(background=True)``.

    Usually unnecessary for "did it finish?" — a completion notification
    is delivered automatically when each job exits. Use this only to
    peek at running state mid-turn (e.g., is it making progress?) or
    after a kill to confirm the job is gone.

    Args:
        job_id: The job to query. Required unless ``list_all=True``.
        list_all: If ``True``, return state for every job known to this
            process (running and finished).

    Returns:
        RichToolOutput. For a single job, metadata contains ``running``,
        ``exit_code`` (``None`` while running), ``pid``, ``log_path``,
        ``log_size_bytes``, ``wall_time_ms``. For ``list_all``, metadata
        contains a ``jobs`` list of the same shape.
    """
    registry = get_registry()

    if list_all:
        jobs = [_job_state_dict(j) for j in registry.list_all()]
        if not jobs:
            llm_output = "No background jobs are tracked in this process."
        else:
            lines = ["Background jobs:"]
            for j in jobs:
                state = "running" if j["running"] else f"exited({j['exit_code']})"
                lines.append(
                    f"  {j['job_id']}  pid={j['pid']}  {state}  {j['wall_time_ms']}ms  {j['command']}"
                )
            llm_output = "\n".join(lines)
        return RichToolOutput(
            llm_output,
            {
                "value": llm_output,
                "display_type": "command",
                "summary": f"{len(jobs)} background job(s)",
                "preview": llm_output[:1000],
                "truncated": False,
                # bash_status exists primarily to refresh the docked panel.
                # Hide its tool card so we don't clutter the message stream.
                "hidden": True,
                "bash_jobs_snapshot": _snapshot_for(ctx),
                "metadata": {"jobs": jobs},
            },
        )

    if not job_id:
        msg = "bash_status requires job_id (or list_all=True)."
        return RichToolOutput(
            msg,
            {
                "value": msg,
                "display_type": "error",
                "summary": "bash_status: missing job_id",
                "preview": msg,
                "truncated": False,
                "metadata": {"error_type": "missing_argument"},
            },
        )

    job = registry.get(job_id)
    if job is None:
        msg = f"No background job tracked with job_id={job_id!r}."
        return RichToolOutput(
            msg,
            {
                "value": msg,
                "display_type": "error",
                "summary": "bash_status: unknown job",
                "preview": msg,
                "truncated": False,
                "bash_jobs_snapshot": _snapshot_for(ctx),
                "metadata": {"error_type": "unknown_job", "job_id": job_id},
            },
        )

    state = _job_state_dict(job)
    if state["running"]:
        summary = (
            f"Job {job_id} running (pid {state['pid']}, {state['wall_time_ms']}ms)"
        )
    else:
        summary = (
            f"Job {job_id} exited({state['exit_code']}) after {state['wall_time_ms']}ms"
        )
    llm_output = (
        f"{summary}\n"
        f"Command: {state['command']}\n"
        f"Logs: {state['log_path']} ({state['log_size_bytes']:,} bytes)"
    )
    return RichToolOutput(
        llm_output,
        {
            "value": llm_output,
            "display_type": "command",
            "summary": summary,
            "preview": llm_output,
            "truncated": False,
            "hidden": True,
            "bash_jobs_snapshot": _snapshot_for(ctx),
            "metadata": state,
        },
    )


# ---------------------------------------------------------------------------
# bash_logs — read a job's log file
# ---------------------------------------------------------------------------


@rich_function_tool(running_summary="Reading background job logs...")
async def bash_logs(
    ctx: RunContextWrapper[Any],
    job_id: str,
    lines: Optional[int] = None,
    from_start: bool = False,
    max_output_chars: Optional[int] = None,
) -> RichToolOutput:
    """
    Returns log output (combined stdout/stderr) for a background bash job.

    Useful for inspecting what a long-running job has printed without
    killing it, or for fetching detailed output after a completion
    notification arrived (the notification carries only the exit code
    and timing — the actual log lines live on disk and are read here).

    Args:
        job_id: The job whose logs to read.
        lines: Number of lines to return (default: 200). Reads from the tail
            unless ``from_start=True``.
        from_start: If ``True``, read the first ``lines`` lines instead of
            the tail.
        max_output_chars: Cap on returned characters (default: 40000). When
            exceeded, the middle is truncated head/tail.

    Returns:
        RichToolOutput. Metadata includes ``job_id``, ``running``,
        ``exit_code``, ``log_path``, ``log_size_bytes``, ``returned_lines``,
        ``returned_chars``, and ``was_truncated``.
    """
    if lines is None:
        lines = _LOGS_DEFAULT_LINES
    if max_output_chars is None:
        max_output_chars = _LOGS_DEFAULT_MAX_CHARS

    job = get_registry().get(job_id)
    if job is None:
        msg = f"No background job tracked with job_id={job_id!r}."
        return RichToolOutput(
            msg,
            {
                "value": msg,
                "display_type": "error",
                "summary": "bash_logs: unknown job",
                "preview": msg,
                "truncated": False,
                "bash_jobs_snapshot": _snapshot_for(ctx),
                "metadata": {"error_type": "unknown_job", "job_id": job_id},
            },
        )

    rc = job.poll_returncode()
    try:
        body, total_lines, log_size_bytes = await read_job_log(
            job, lines=lines, from_start=from_start
        )
    except OSError as e:
        msg = f"Failed to read log file {job.log_path}: {e}"
        return RichToolOutput(
            msg,
            {
                "value": msg,
                "display_type": "error",
                "summary": "bash_logs: read error",
                "preview": msg,
                "truncated": False,
                "bash_jobs_snapshot": _snapshot_for(ctx),
                "metadata": {
                    "error_type": "log_read_error",
                    "job_id": job_id,
                    "error": str(e),
                },
            },
        )

    selected = body.split("\n") if body else []

    body, lines_truncated = _truncate_long_lines(body, _BASH_MAX_LINE_LENGTH)
    body, chars_removed, was_truncated = _truncate_output_head_tail(
        body, max_output_chars
    )

    state = "running" if rc is None else f"exited({rc})"
    header_lines = [
        f"Job {job_id}: {state} | {job.wall_time_ms()}ms | {job.log_path}",
        f"Showing {len(selected)} of {total_lines} log lines"
        + (" (from start)" if from_start else " (tail)"),
    ]
    if lines_truncated:
        header_lines.append(
            f"[{lines_truncated} lines exceeded {_BASH_MAX_LINE_LENGTH} chars and were truncated]"
        )
    if was_truncated:
        header_lines.append(
            f"[Output truncated: {chars_removed:,} characters removed from middle]"
        )

    header = "\n".join(header_lines) + "\n\n"
    llm_output = header + (body if body else "(no log output)")

    ui_metadata = {
        "value": body,
        "display_type": "command",
        "summary": f"Logs for {job_id} ({state})",
        "preview": "\n".join(selected[:20]),
        "truncated": was_truncated or lines_truncated > 0,
        "bash_jobs_snapshot": _snapshot_for(ctx),
        "metadata": {
            "job_id": job_id,
            "running": rc is None,
            "exit_code": rc,
            "log_path": job.log_path,
            "log_size_bytes": log_size_bytes,
            "total_lines": total_lines,
            "returned_lines": len(selected),
            "returned_chars": len(body),
            "chars_truncated": chars_removed,
            "lines_truncated": lines_truncated,
            "was_truncated": was_truncated,
            "from_start": from_start,
        },
    }
    return RichToolOutput(llm_output, ui_metadata)


# ---------------------------------------------------------------------------
# bash_kill — terminate a background job
# ---------------------------------------------------------------------------


@rich_function_tool(running_summary="Terminating background job...")
async def bash_kill(
    ctx: RunContextWrapper[Any],
    job_id: str,
    grace_seconds: Optional[int] = None,
) -> RichToolOutput:
    """
    Terminates a background bash job. Sends SIGTERM, waits up to
    ``grace_seconds`` (default 5), then escalates to SIGKILL if the process
    is still alive. No-op if the job has already exited.

    Args:
        job_id: The job to terminate.
        grace_seconds: Seconds to wait between SIGTERM and SIGKILL (default 5).

    Returns:
        RichToolOutput with the final job state in metadata, including the
        ``signal_sent`` field (``"none"``, ``"SIGTERM"``, or ``"SIGKILL"``).
    """
    if grace_seconds is None:
        grace_seconds = _KILL_DEFAULT_GRACE_SECONDS

    job = get_registry().get(job_id)
    if job is None:
        msg = f"No background job tracked with job_id={job_id!r}."
        return RichToolOutput(
            msg,
            {
                "value": msg,
                "display_type": "error",
                "summary": "bash_kill: unknown job",
                "preview": msg,
                "truncated": False,
                "bash_jobs_snapshot": _snapshot_for(ctx),
                "metadata": {"error_type": "unknown_job", "job_id": job_id},
            },
        )

    rc = job.poll_returncode()
    if rc is not None:
        state = _job_state_dict(job)
        state["signal_sent"] = "none"
        msg = f"Job {job_id} already exited with code {rc}; nothing to kill."
        return RichToolOutput(
            msg,
            {
                "value": msg,
                "display_type": "command",
                "summary": f"Job {job_id} already exited",
                "preview": msg,
                "truncated": False,
                # No-op kill — the panel already shows the job as exited; no
                # reason to add a chat bubble that says the same thing.
                "hidden": True,
                "bash_jobs_snapshot": _snapshot_for(ctx),
                "metadata": state,
            },
        )

    try:
        signal_sent = await terminate_job(job, grace_seconds=grace_seconds)
    except Exception as e:
        msg = f"Failed to terminate job {job_id}: {e}"
        return RichToolOutput(
            msg,
            {
                "value": msg,
                "display_type": "error",
                "summary": "bash_kill: termination error",
                "preview": msg,
                "truncated": False,
                "bash_jobs_snapshot": _snapshot_for(ctx),
                "metadata": {
                    "error_type": "kill_error",
                    "job_id": job_id,
                    "error": str(e),
                },
            },
        )

    state = _job_state_dict(job)
    state["signal_sent"] = signal_sent

    final_rc = state["exit_code"]
    if state["running"]:
        summary = f"Job {job_id} still running after {signal_sent}"
    else:
        summary = f"Job {job_id} terminated (signal {signal_sent}, exit {final_rc})"
    llm_output = f"{summary}\nLogs: {state['log_path']}"

    return RichToolOutput(
        llm_output,
        {
            "value": llm_output,
            "display_type": "command",
            "summary": summary,
            "preview": llm_output,
            "truncated": False,
            # The panel transition (row goes strikethrough, dot turns green/red)
            # is the user-visible signal for a successful kill. Skip the chat
            # bubble — it'd just duplicate the panel. The model still sees the
            # full tool result via its prompt history.
            "hidden": True,
            "bash_jobs_snapshot": _snapshot_for(ctx),
            "metadata": state,
        },
    )


# ---------------------------------------------------------------------------
# Approval metadata — render commands nicely in approval dialogs
# ---------------------------------------------------------------------------


@approval_metadata()
def execute_bash_approval(
    command: str,
    timeout: Optional[int] = None,
    background: bool = False,
) -> dict:
    summary = "Run shell command (background)" if background else "Run shell command"
    preview = f"$ {command}" + ("  &" if background else "")
    return {
        "display_type": "command",
        "summary": summary,
        "value": command,
        "preview": preview,
        "truncated": False,
        "metadata": {
            "command": command,
            "timeout": timeout,
            "background": background,
        },
    }


@approval_metadata()
def bash_kill_approval(job_id: str, grace_seconds: Optional[int] = None) -> dict:
    return {
        "display_type": "command",
        "summary": f"Terminate background job {job_id}",
        "value": job_id,
        "preview": f"kill {job_id}",
        "truncated": False,
        "metadata": {"job_id": job_id, "grace_seconds": grace_seconds},
    }
