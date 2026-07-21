"""User-overridable prompt-file resolution.

Products often ship a builtin prompt (a loop body, a recap template, a
maintenance checklist) that operators should be able to override with a
file — workspace-local first, then user config, then the builtin text.
:func:`resolve_prompt_file` is that resolution, size-capped so a stray
multi-megabyte file can't blow up a context window.

Lifted from omni-code's ``omni_code/loop_prompts.py``; the builtin
prompt text itself stays product-side (it is product policy), only the
resolution mechanics live here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

__all__ = ["DEFAULT_MAX_PROMPT_FILE_BYTES", "resolve_prompt_file"]

# Generous for prose, small enough that a mistaken binary/log file can't
# flood a model context.
DEFAULT_MAX_PROMPT_FILE_BYTES = 25_000


def resolve_prompt_file(
    candidates: Sequence[Path],
    fallback: str,
    *,
    max_bytes: int = DEFAULT_MAX_PROMPT_FILE_BYTES,
) -> str:
    """Return the first non-empty candidate file's text, else *fallback*.

    Candidates are tried in order (callers list workspace-local paths
    before user-config paths). A candidate is used when it exists, is a
    regular file, and has non-whitespace content; its bytes are truncated
    to *max_bytes* before decoding (utf-8, undecodable bytes replaced)
    and the result is stripped. Unreadable candidates are skipped rather
    than raised — an operator's broken override should degrade to the
    builtin, not take the feature down.
    """
    for path in candidates:
        try:
            if path.exists() and path.is_file():
                data = path.read_bytes()[:max_bytes]
                text = data.decode("utf-8", errors="replace").strip()
                if text:
                    return text
        except Exception:
            continue
    return fallback
