# Intentionally empty — package marker only.
#
# Keep this file free of re-exports. Historically a cascade of
# `from .bash_tool import ...` re-exports here bound sys.modules['tools']
# in a way that collided with omniagents' builtin tool discovery (both
# used the bare `tools` module prefix). omniagents ≥0.7.19 resolves
# canonical dotted module names during discovery, so the collision itself
# is fixed framework-side — but nothing in this repo imports symbols from
# `tools` directly (callers use `from tools.<module> import X`), so
# re-exports would be dead weight anyway.
