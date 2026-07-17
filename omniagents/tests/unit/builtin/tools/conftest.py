"""Fixtures for builtin-tool tests, including background bash jobs.

Background jobs spawn real asyncio subprocesses; their I/O machinery is
bound to whichever event loop created them, so every ``invoke_tool``
call in a test must run on one shared loop (see ``test_event_loop``).

The fixture bodies live in ``omniagents.testing`` so downstream products
can reuse them; importing them here scopes them (including the autouse
permissive workspace) to this directory.
"""

from omniagents.testing import (  # noqa: F401
    _default_permissive_workspace,
    invoke_tool,
    test_event_loop,
)
