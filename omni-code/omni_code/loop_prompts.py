from __future__ import annotations

from pathlib import Path

from omniagents.core.utils.prompt_files import resolve_prompt_file

from omniagents.core.config import product_config_dir

MAX_LOOP_MD_BYTES = 25_000


BUILTIN_MAINTENANCE_PROMPT = """Continue useful maintenance for this session.

Work in this order:
1. Continue any unfinished work already authorized in the conversation.
2. Tend to the current branch or pull request when discoverable: failed checks, review comments, merge conflicts, or obvious regressions.
3. Check background jobs, workers, and queued follow-up work that the transcript already started.
4. If nothing needs action, say so briefly and stop.

Do not start unrelated new initiatives. Do not push, delete, merge, or perform irreversible actions unless the transcript already authorized that exact class of action.""".strip()


def resolve_default_loop_prompt(workspace_root: str | None = None) -> str:
    return resolve_prompt_file(
        _candidate_paths(workspace_root),
        BUILTIN_MAINTENANCE_PROMPT,
        max_bytes=MAX_LOOP_MD_BYTES,
    )


def _candidate_paths(workspace_root: str | None) -> list[Path]:
    paths: list[Path] = []
    if workspace_root:
        paths.append(Path(workspace_root).expanduser() / ".omni" / "loop.md")
    paths.append(product_config_dir() / "loop.md")
    return paths
