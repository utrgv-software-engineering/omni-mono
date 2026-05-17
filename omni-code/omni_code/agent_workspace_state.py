"""Process-global manifest root for the running omni serve session.

``serve_cli._serve`` sets this once after ``build_manifest`` so server
functions like ``fs_get_workspace_root`` can report the path the agent's
tools actually see — e.g. ``/workspace`` inside a docker session — rather
than omni serve's host cwd. For unix_local profiles the manifest root
equals the host workspace dir, so the value matches the host path in
that case anyway; this only matters for clients whose manifest root
differs from the host process cwd.
"""

from __future__ import annotations

from typing import Optional

_manifest_root: Optional[str] = None


def set_manifest_root(root: Optional[str]) -> None:
    global _manifest_root
    _manifest_root = root


def get_manifest_root() -> Optional[str]:
    return _manifest_root
