"""Async supervisor; worker JS never owns tool/session identity or authorization."""

from __future__ import annotations
from agents.exceptions import (
    ToolInputGuardrailTripwireTriggered,
    ToolOutputGuardrailTripwireTriggered,
)

TRIPWIRES = (ToolInputGuardrailTripwireTriggered, ToolOutputGuardrailTripwireTriggered)
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile

FRAME_BYTES = 2_000_000
OUTPUT_BYTES = 40_000


async def execute(
    code, dispatch, *, cpu_seconds=5.0, watchdog_seconds=15.0, on_output=None
):
    if not isinstance(code, str) or len(code.encode()) > 100_000:
        raise ValueError("JavaScript source must be at most 100000 bytes")
    outputs: list[str] = []
    errors: list[str] = []
    media: list[dict] = []
    emitted = 0
    pending: set[asyncio.Task] = set()
    fatal = asyncio.get_running_loop().create_future()
    semaphore = asyncio.Semaphore(8)
    write_lock = asyncio.Lock()
    # Deliberately do not inherit provider credentials, PYTHONPATH, or startup hooks.
    env = {
        key: os.environ[key]
        for key in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP")
        if key in os.environ
    }
    with tempfile.TemporaryDirectory(prefix="omni-js-") as directory:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            str(Path(__file__).with_name("worker.py")),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            cwd=directory,
            env=env,
            limit=FRAME_BYTES + 1,
        )

        stdin, stdout = process.stdin, process.stdout
        assert stdin is not None and stdout is not None

        async def send(message):
            data = (
                json.dumps(message, ensure_ascii=True, allow_nan=False).encode() + b"\n"
            )
            if len(data) > FRAME_BYTES:
                data = (
                    json.dumps(
                        {
                            "id": message.get("id"),
                            "error": "Result exceeds IPC limit; use session.results.list/read to retrieve retained output",
                        }
                    ).encode()
                    + b"\n"
                )
            async with write_lock:
                stdin.write(data)
                await stdin.drain()

        async def call(request):
            try:
                async with semaphore:
                    value = await dispatch(request["method"], request.get("params", {}))
                await send({"id": request["id"], "value": value})
            except TRIPWIRES as exc:
                if not fatal.done():
                    fatal.set_result(exc)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                errors.append(str(exc)[:1000])
                try:
                    await send({"id": request["id"], "error": str(exc)[:1000]})
                except (BrokenPipeError, ConnectionResetError):
                    pass

        async def read():
            # Pending approvals/tools have their own cancellation lifecycle. Do not
            # charge their wait against active JS CPU or the compute watchdog.
            task = asyncio.create_task(stdout.readline())
            try:
                while True:
                    done, _ = await asyncio.wait(
                        {task, fatal},
                        timeout=watchdog_seconds,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if fatal in done:
                        raise fatal.result()
                    if task in done:
                        return task.result()
                    if not pending:
                        raise TimeoutError("JavaScript worker watchdog expired")
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

        try:
            await send({"version": 1, "code": code, "cpu_seconds": cpu_seconds})
            count = 0
            while True:
                line = await read()
                if not line:
                    raise RuntimeError(
                        "JavaScript worker exited unexpectedly; dispatched tools are not replayed"
                    )
                if len(line) > FRAME_BYTES:
                    raise RuntimeError("IPC frame exceeded")
                frame = json.loads(line)
                if "fatal" in frame:
                    raise RuntimeError(frame["fatal"])
                for request in frame.get("requests", []):
                    if request.get("method") == "media":
                        from agents.tool import ToolOutputImage, ToolOutputFileContent

                        value = request.get("value", {})
                        model = (
                            ToolOutputImage
                            if value.get("type") == "image"
                            else ToolOutputFileContent
                        )
                        if (
                            len(media) >= 8
                            or len(json.dumps(value).encode()) > 1_000_000
                        ):
                            raise ValueError("Media emission limit exceeded")
                        media.append(
                            model.model_validate(value).model_dump(exclude_none=True)
                        )
                        continue
                    if request.get("method") == "text":
                        value = str(request.get("value", ""))
                        available = max(
                            0, OUTPUT_BYTES - emitted - (1 if outputs else 0)
                        )
                        encoded = value.encode()
                        if available:
                            emitted += (1 if outputs else 0) + min(
                                available, len(encoded)
                            )
                            outputs.append(encoded[:available].decode(errors="ignore"))
                        if (
                            len(encoded) > available
                            and "Emitted output truncated" not in errors
                        ):
                            errors.append("Emitted output truncated")
                        continue
                    count += 1
                    if count > 256:
                        raise RuntimeError("Call limit exceeded")
                    task = asyncio.create_task(call(request))
                    pending.add(task)
                    task.add_done_callback(pending.discard)
                if on_output is not None:
                    await on_output(
                        {
                            "output": "\n".join(outputs),
                            "errors": list(errors),
                            "media": list(media),
                        }
                    )
                if frame.get("done") and not frame.get("pending"):
                    if frame.get("error"):
                        errors.append(frame["error"])
                    break
            if pending:
                await asyncio.gather(*pending)
            return {"output": "\n".join(outputs), "errors": errors, "media": media}
        finally:
            # Already started calls retain durable dispatch claims even when cancelled;
            # absence of a completion never authorizes automatic re-execution.
            for task in list(pending):
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            if process.returncode is None:
                process.kill()
            await process.wait()
