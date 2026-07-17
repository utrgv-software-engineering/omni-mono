"""Shared test fixtures — bodies live in ``omniagents.testing``.

``_default_permissive_workspace`` is autouse and applies to the whole
suite; ``invoke_tool``/``test_event_loop`` mirror the W0 host-runtime
contract for tests that drive tools directly.
"""

from omniagents.testing import (  # noqa: F401
    _default_permissive_workspace,
    invoke_tool,
    test_event_loop,
)
