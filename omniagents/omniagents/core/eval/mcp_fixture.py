"""Per-scenario user-MCP fixture staging for eval environments.

Scenarios that exercise an agent's user-configured MCP servers
(``allow_user_mcp_servers``) need an isolated ``XDG_CONFIG_HOME`` inside
the scenario's temp dir: a generated ``<product>/mcp.json`` pointing a
real MCP server at per-scenario state (a copied database fixture, a
staged pages tree, ...) instead of the user's real config. This module
owns that staging machinery; products keep only their parts — which
server binary, which fixture files.

Typical use inside an ``EvalEnvironment.setup``::

    stager = UserMcpFixtureStager(tmp_dir, product_config_name="omni_code")
    db_path = stager.stage_file(fixture_db, "projects.db")      # or planned path
    pages_dir = stager.stage_tree(fixture_pages, "projects")
    stager.write_mcp_json({
        "omni-projects": {
            "type": "stdio",
            "command": "node",
            "args": [cli, "--db-path", str(db_path), "--pages-dir", str(pages_dir)],
        }
    })
    # later, in get_env_vars():
    env.update(stager.env_vars())   # {"XDG_CONFIG_HOME": ...}

Why ``env_vars()`` instead of mutating ``os.environ`` in ``setup()``:
the eval runner applies ``get_env_vars()`` to the process environment
*before* loading the agent spec (which is when user-MCP config is
discovered), with no awaits in between — so under asyncio concurrency
each scenario's spec load reads its own ``XDG_CONFIG_HOME`` and no
cross-scenario lock is needed. Values read *later* in the run are
subject to the runner's generic save/apply/restore interleaving across
concurrent scenarios (true of every environment-declared env var);
generated ``mcp.json`` files should therefore embed absolute paths, as
above, rather than relying on the environment at server-spawn time.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Dict, Mapping

__all__ = ["UserMcpFixtureStager"]


class UserMcpFixtureStager:
    """Stage an isolated ``XDG_CONFIG_HOME`` with a generated mcp.json.

    Args:
        tmp_dir: The scenario's temp/workspace directory. The isolated
            config root is created under it (so teardown of the temp dir
            removes the staging too).
        product_config_name: The product's config-dir name — mcp.json is
            written to ``<xdg_root>/<product_config_name>/mcp.json``,
            matching the framework's user-MCP discovery
            (``omniagents.core.config.user_mcp``).
        xdg_subdir: Name of the config root inside *tmp_dir*.
    """

    def __init__(
        self,
        tmp_dir: str | Path,
        *,
        product_config_name: str,
        xdg_subdir: str = "_xdg_config",
    ) -> None:
        self.xdg_root = Path(tmp_dir) / xdg_subdir
        self.config_dir = self.xdg_root / product_config_name
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.mcp_json_path = self.config_dir / "mcp.json"

    def stage_file(self, src: Path | None, dest_name: str) -> Path:
        """Plan (and copy, when *src* exists) a file into the config dir.

        Returns the destination path either way, so callers can embed it
        in server args even when the server initializes the file itself
        on first open (e.g. a fresh database schema).
        """
        dest = self.config_dir / dest_name
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src is not None and Path(src).is_file():
            shutil.copy2(src, dest)
        return dest

    def stage_tree(self, src: Path | None, dest_name: str) -> Path:
        """Create a directory in the config dir, copying *src* into it if
        it exists. Returns the destination directory path."""
        dest = self.config_dir / dest_name
        dest.mkdir(parents=True, exist_ok=True)
        if src is not None and Path(src).is_dir():
            shutil.copytree(src, dest, dirs_exist_ok=True)
        return dest

    def write_mcp_json(self, servers: Mapping[str, Any]) -> Path:
        """Write ``mcp.json`` with the given ``mcpServers`` mapping.

        *servers* maps server name to its config dict (``type``,
        ``command``, ``args``, ...). Embed absolute paths in ``args`` —
        see the module docstring for why.
        """
        payload = {"mcpServers": dict(servers)}
        self.mcp_json_path.write_text(json.dumps(payload, indent=2) + "\n")
        return self.mcp_json_path

    def env_vars(self) -> Dict[str, str]:
        """Env vars to merge into ``get_env_vars()`` so spec loading sees
        the staged config."""
        return {"XDG_CONFIG_HOME": str(self.xdg_root)}
