"""Unit tests for the ACI heredoc write/hydrate command construction.

The exec channel sends each command as one PTY line, and canonical-mode line
discipline truncates input past ~4 KB. write()/hydrate_workspace() therefore
feed the base64 payload as many short heredoc lines; these tests pin the
wrapping + command shape (live byte-exactness is covered by integration runs).
"""

from __future__ import annotations

import pytest

pytest.importorskip("azure.identity")
pytest.importorskip("azure.mgmt.containerinstance")

import base64  # noqa: E402

from omniagents.core.sandbox.aci import (  # noqa: E402
    _B64_HEREDOC_MARKER,
    _b64_heredoc_lines,
)


def test_wraps_at_76_cols_and_roundtrips():
    raw = bytes(range(256)) * 64  # 16 KB, well over the PTY line limit
    body = _b64_heredoc_lines(raw)
    lines = body.split("\n")
    assert all(len(ln) <= 76 for ln in lines)
    assert max(len(ln) for ln in lines) == 76  # actually wrapped, not one line
    # The heredoc body is decodable back to the original bytes.
    assert base64.b64decode("".join(lines)) == raw


def test_empty_payload():
    assert _b64_heredoc_lines(b"") == ""


def test_marker_cannot_collide_with_body():
    # base64's alphabet never contains '_', so the marker is collision-safe.
    assert "_" in _B64_HEREDOC_MARKER
    body = _b64_heredoc_lines(b"some bytes here \x00\xff")
    assert _B64_HEREDOC_MARKER not in body
