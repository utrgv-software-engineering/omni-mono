"""Filesystem helpers shared across the framework."""

from __future__ import annotations

import os
import shutil
import stat
import sys
from pathlib import Path
from typing import Any, Callable


def remove_tree(path: str | os.PathLike[str], *, ignore_errors: bool = False) -> None:
    """Delete a directory tree, including read-only files.

    Windows refuses to delete read-only files, and git marks its object
    files read-only, so a plain ``shutil.rmtree`` fails on any tree that
    holds a repository. A failed delete is retried once after clearing the
    read-only bit.
    """

    def retry_writable(func: Callable[..., Any], target: str, _exc: Any) -> None:
        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
        except Exception:
            if not ignore_errors:
                raise

    if sys.version_info >= (3, 12):
        shutil.rmtree(Path(path), onexc=retry_writable)
    else:
        shutil.rmtree(Path(path), onerror=retry_writable)
