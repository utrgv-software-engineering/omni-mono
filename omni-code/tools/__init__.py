# Intentionally empty.
#
# Re-exports were removed because they triggered a sys.modules collision
# during omniagents eval discovery: when this __init__.py loaded, its
# cascade of `from .bash_tool import ...` imports bound `sys.modules['tools']`
# to this directory, which then prevented `omniagents.core.tools.discovery`
# from importing the builtin tools at `omniagents/builtin/tools/` (it tries
# `importlib.import_module('tools.file_tools')` and Python locks onto the
# already-cached `tools` package). The result was that the agent ran without
# `read_file`, `list_directory`, `apply_patch`, `write_file`, and the other
# builtin tools — and reached for `read_page` (omni-projects MCP) as the
# closest-named substitute.
#
# Nothing in this repo imports symbols directly from `tools` — all callers
# use `from tools.<module> import X`. Removing the re-exports is safe.
