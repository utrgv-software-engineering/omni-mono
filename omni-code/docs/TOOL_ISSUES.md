# Tool Import Collision

## Problem
- `main.py` and several agents import the project toolset with `from tools import *`.
- To make that work after packaging, we previously declared the `tools/` directory as a top-level package.
- OmniAgents projects also require their own `tools/` folder. When `omni` runs inside a scaffolded project, Python resolves that local package first, so Omni Code loses access to its tool implementations (e.g., `invoke_slash_command`).

## Proposed Fix
- Stop exporting the Omni Code tool suite as a package. Keep the directory named exactly `tools/`, but treat it as project-local data.
- Update `main.py` (and any helper factories) to load the tool modules explicitly via `importlib` relative to the repository root instead of relying on `PYTHONPATH` or `sys.path` order.
- Ship the tool files as package data so the wheel still contains them without claiming a global module name.

## Failure Modes Observed
- I attempted to solve the collision by creating a new package (`omni_code.project_tools`), which still required exporting a module and preserved the core namespace conflict.
- I then layered on another alias package (`omni_code.tools_modules`), compounding the mistake and leaving the collision unresolved.
- None of those changes addressed the root issue: the wildcard import that assumes `tools` is globally unique. Until the import is rewritten to target a private namespace, the collision will continue.
