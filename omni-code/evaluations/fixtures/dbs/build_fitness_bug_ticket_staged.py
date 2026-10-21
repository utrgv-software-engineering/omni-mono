#!/usr/bin/env python3
"""Build evaluations/fixtures/dbs/fitness_bug_ticket_staged.db.

Same project + ticket as ``build_fitness_bug_ticket.py``, but seeds a
multi-stage pipeline (Backlog → Planning → Implementation → QA →
Review → Done) with a documentation-level gate on ``Review``. Used
by the staged-kanban variant of the autopilot fitness scenario.

Re-run any time the schema or seed text changes:

    python evaluations/fixtures/dbs/build_fitness_bug_ticket_staged.py
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import sqlite3
import string
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict


_ID_ALPHABET = string.ascii_letters + string.digits + "_-"


def _nano(n: int = 12) -> str:
    """Mirror the nanoid format used by omni-projects-db (URL-safe, 12 chars)."""
    return "".join(secrets.choice(_ID_ALPHABET) for _ in range(n))


# (label, gate) — gate=1 marks the "human approval required" boundary.
# The MCP itself does not enforce gates today; the eval enforces it via
# until_sql + assert_sql + a `did_not_advance_past_review` measure.
_STAGED_COLUMNS: tuple[tuple[str, int], ...] = (
    ("Backlog", 0),
    ("Planning", 0),
    ("Implementation", 0),
    ("QA", 0),
    ("Review", 1),
    ("Done", 0),
)


_LAUNCHER_DEFAULT_MCP_CLI = (
    Path.home()
    / "Omni"
    / "Workspace"
    / "launcher"
    / "packages"
    / "projects-mcp"
    / "dist"
    / "cli.js"
)
_OUTPUT_DB = Path(__file__).parent / "fitness_bug_ticket_staged.db"


_BUG_TITLE = "Recent workouts endpoint returns wrong order"
_BUG_DESCRIPTION = """\
The Dashboard's "Recent Workouts" table shows sessions in the wrong order.
It should list the most-recent workout_date first, but it sorts by record
creation time (created_at). For backdated entries this breaks visibly: a
session that happened 5 days ago but was logged today shows up at the top
of the dashboard table, ahead of yesterday's workout.

Root cause: GET /api/v1/workouts/recent (src/fitness_tracker/routers/workouts.py
::get_recent_workouts) orders by Workout.created_at instead of
Workout.workout_date. The Dashboard (frontend/src/pages/Dashboard.tsx) calls
workoutsApi.recent() and renders the response directly, so the wrong order
shows up there.

Repro:
  1. uv sync && npm --prefix frontend ci
  2. uv run python -m fitness_tracker.seed
  3. Start backend + frontend (Procfile.dev, or uvicorn on :8000 + `npm --prefix
     frontend run dev` on :5173).
  4. Open the dashboard at http://127.0.0.1:5173/. The "Recent Workouts"
     table shows backdated entries above more-recent ones.

Acceptance — all three layers required:
  1. Source fix: change src/fitness_tracker/routers/workouts.py::
     get_recent_workouts to order by workout_date instead of created_at.
  2. API regression test in tests/test_workouts.py: create workouts whose
     created_at and workout_date order disagree, then assert /recent returns
     them in workout_date order (most recent first). The existing
     test_get_recent_workouts is a false green — it inserts in chronological
     order so the wrong sort still passes.
  3. UI acceptance story under acceptance/stories/*.yaml driving the
     dashboard with the webapp-acceptance-runner skill. Use
     .omni_code/skills/webapp-acceptance-runner/scripts/install_acceptance.py
     to scaffold the runner. Story must seed two workouts where created_at
     and workout_date disagree (e.g. via the API), open the dashboard, and
     assert the first row in the Recent Workouts table is the
     most-recent-by-workout_date entry. Use playwright-cli (already on PATH)
     for any ad-hoc UI exploration.

Verification: `uv run pytest -q` passes AND the acceptance runner reports
all stories passing.
"""


def _resolve_mcp_cli() -> Path:
    env_path = os.environ.get("OMNI_PROJECTS_MCP_CLI")
    if env_path:
        p = Path(env_path).expanduser()
        if p.is_file():
            return p
        sys.exit(f"OMNI_PROJECTS_MCP_CLI={env_path!r} does not point to a file")
    if _LAUNCHER_DEFAULT_MCP_CLI.is_file():
        return _LAUNCHER_DEFAULT_MCP_CLI
    sys.exit(
        "omni-projects-mcp cli not found. Set OMNI_PROJECTS_MCP_CLI or build "
        f"the launcher at {_LAUNCHER_DEFAULT_MCP_CLI}"
    )


class _StdioRpc:
    """Tiny JSON-RPC-over-stdio client for the MCP cli."""

    def __init__(self, proc: subprocess.Popen) -> None:
        self._proc = proc
        self._next_id = 0

    def call(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        self._next_id += 1
        request = {
            "jsonrpc": "2.0",
            "id": self._next_id,
            "method": method,
            "params": params,
        }
        assert self._proc.stdin is not None and self._proc.stdout is not None
        self._proc.stdin.write(json.dumps(request) + "\n")
        self._proc.stdin.flush()
        # Skip stderr-level chatter; read the next JSON-RPC line from stdout.
        line = self._proc.stdout.readline()
        if not line:
            raise RuntimeError(f"MCP cli closed stdout before responding to {method}")
        msg = json.loads(line)
        if "error" in msg:
            raise RuntimeError(f"MCP error on {method}: {msg['error']}")
        return msg.get("result", {})

    def tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        result = self.call("tools/call", {"name": name, "arguments": arguments})
        # MCP wraps tool output as content blocks; our tools return a single
        # text block whose body is JSON.
        content = result.get("content") or []
        if not content:
            return result
        text = content[0].get("text", "")
        return json.loads(text) if text else {}


def _spawn_cli(cli_path: Path, db_path: Path, pages_dir: Path) -> subprocess.Popen:
    return subprocess.Popen(
        [
            "node",
            str(cli_path),
            "--db-path",
            str(db_path),
            "--pages-dir",
            str(pages_dir),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )


def _shutdown_cli(proc: subprocess.Popen) -> None:
    try:
        if proc.stdin:
            proc.stdin.close()
    except Exception:
        pass
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def _replace_pipeline_with_staged(db_path: Path, project_id: str) -> None:
    """Overwrite the cli's auto-seeded pipeline with the staged 6-column
    workflow: Backlog → Planning → Implementation → QA → Review → Done.

    ``Review`` carries ``gate=1`` to mark it as the human-approval
    boundary. The MCP does not enforce gates today (move_ticket accepts
    any valid label regardless of gate); the eval enforces the boundary
    via ``until_sql`` + ``assert_sql`` + a ``did_not_advance_past_review``
    measure.
    """
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "DELETE FROM pipeline_columns WHERE project_id = ?", (project_id,)
        )
        for sort_order, (label, gate) in enumerate(_STAGED_COLUMNS):
            conn.execute(
                "INSERT INTO pipeline_columns "
                "(id, project_id, label, description, sort_order, gate) "
                "VALUES (?, ?, ?, NULL, ?, ?)",
                (f"col_{_nano()}", project_id, label, sort_order, gate),
            )
        # Bump the change sequence so any reader (including the cli on its
        # next open) sees fresh state.
        conn.execute("UPDATE _change_seq SET seq = seq + 1")
        conn.commit()
    finally:
        conn.close()


def main() -> None:
    cli_path = _resolve_mcp_cli()
    if _OUTPUT_DB.exists():
        _OUTPUT_DB.unlink()

    scratch = Path(tempfile.mkdtemp(prefix="fitness_bug_seed_"))
    pages_dir = scratch / "pages"
    pages_dir.mkdir()

    # Phase 1: create project via cli (gets schema migrations + root page).
    proc = _spawn_cli(cli_path, _OUTPUT_DB, pages_dir)
    try:
        rpc = _StdioRpc(proc)
        rpc.call(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "fitness-bug-seeder", "version": "0"},
            },
        )
        project = rpc.tool("create_project", {"label": "Fitness Tracker"})
        project_id = project["id"]
        print(
            f"phase 1 — created project: id={project_id} "
            f"default_pipeline={project['pipeline']}"
        )
    finally:
        _shutdown_cli(proc)

    # Phase 2: overwrite the auto-seeded pipeline with the staged columns.
    _replace_pipeline_with_staged(_OUTPUT_DB, project_id)
    print(f"phase 2 — replaced pipeline with: {[c[0] for c in _STAGED_COLUMNS]}")

    # Phase 3: create the ticket via cli — auto-lands in the new "Backlog".
    proc = _spawn_cli(cli_path, _OUTPUT_DB, pages_dir)
    try:
        rpc = _StdioRpc(proc)
        rpc.call(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "fitness-bug-seeder", "version": "0"},
            },
        )
        ticket = rpc.tool(
            "create_ticket",
            {
                "project_id": project_id,
                "title": _BUG_TITLE,
                "description": _BUG_DESCRIPTION,
                "priority": "high",
            },
        )
        print(
            f"phase 3 — created ticket: id={ticket['id']} "
            f"column={ticket['column']}"
        )
    finally:
        _shutdown_cli(proc)

    shutil.rmtree(scratch, ignore_errors=True)
    print(f"wrote {_OUTPUT_DB} ({_OUTPUT_DB.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
