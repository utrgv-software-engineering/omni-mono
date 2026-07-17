from __future__ import annotations

from pathlib import Path

MAX_LOOP_MD_BYTES = 25_000


BUILTIN_MAINTENANCE_PROMPT = """Continue useful maintenance for this session.

Work in this order:
1. Continue any unfinished work already authorized in the conversation.
2. Tend to the current branch or pull request when discoverable: failed checks, review comments, merge conflicts, or obvious regressions.
3. Check background jobs, workers, and queued follow-up work that the transcript already started.
4. If nothing needs action, say so briefly and stop.

Do not start unrelated new initiatives. Do not push, delete, merge, or perform irreversible actions unless the transcript already authorized that exact class of action.""".strip()


def resolve_default_loop_prompt(workspace_root: str | None = None) -> str:
    for path in _candidate_paths(workspace_root):
        try:
            if path.exists() and path.is_file():
                data = path.read_bytes()[:MAX_LOOP_MD_BYTES]
                text = data.decode("utf-8", errors="replace").strip()
                if text:
                    return text
        except Exception:
            continue
    return BUILTIN_MAINTENANCE_PROMPT


def _candidate_paths(workspace_root: str | None) -> list[Path]:
    paths: list[Path] = []
    if workspace_root:
        paths.append(Path(workspace_root).expanduser() / ".omni" / "loop.md")
    paths.append(Path.home() / ".config" / "omni_code" / "loop.md")
    return paths
