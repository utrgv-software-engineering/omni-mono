"""Explicit opt-in for factory improvement automations; unavailable policy denies work."""

import asyncio
import json
import os
from urllib.request import Request, urlopen


def policy():
    url = os.environ.get("FACTORY_SETTINGS_URL")
    if url:
        try:
            request = Request(
                url,
                headers={
                    "Authorization": "Bearer "
                    + os.environ.get("FACTORY_SETTINGS_TOKEN", "")
                },
            )
            with urlopen(request, timeout=5) as response:
                value = json.load(response)
            if (
                isinstance(value, dict)
                and value.get("revision")
                and value["revision"] != os.environ.get("FACTORY_REPOSITORY_REVISION")
            ):
                value["enabled"] = (
                    False  # The runtime must load the selected provider/repositories first.
                )
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}
    return {
        "enabled": os.environ.get("FACTORY_SELF_IMPROVEMENT", "").lower() == "true",
        "repository": os.environ.get("FACTORY_LINKED_REPOSITORY", ""),
    }


def is_improvement(automation):
    # Legacy factories declared the improver role before the explicit flag existed.
    return automation.self_improvement or automation.agent == "improver"


async def allowed(automation):
    if not is_improvement(automation):
        return True
    value = await asyncio.to_thread(policy)
    return value.get("enabled") is True and bool(value.get("repository"))


async def guarded_run(run, automation, *args):
    """Recheck pending work and cancel only this automation when opt-in is revoked."""
    from .intake import RunResult

    initial = await asyncio.to_thread(policy) if is_improvement(automation) else {}
    if is_improvement(automation) and not (
        initial.get("enabled") is True and initial.get("repository")
    ):
        return RunResult(
            error="Self-improvement is disabled or its factory repository is unavailable",
            stopped=True,
        )
    if is_improvement(automation):
        agent, session_id, prompt, progress = args
        args = (
            agent,
            session_id,
            f"Self-improvement target: {initial['repository']}. Only this linked repository is the factory's own repository.\n\n"
            + prompt,
            progress,
        )
    task = asyncio.create_task(run(*args))
    try:
        if not is_improvement(automation):
            return await task
        while not task.done():
            done, _ = await asyncio.wait({task}, timeout=2)
            if done:
                break
            current = await asyncio.to_thread(policy)
            if (
                current.get("enabled") is not True
                or current.get("repository") != initial.get("repository")
                or current.get("provider") != initial.get("provider")
            ):
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                return RunResult(
                    error="Self-improvement was disabled or repository access was lost",
                    stopped=True,
                )
        return await task
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
