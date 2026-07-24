"""Auto-export scenario transcripts to disk for post-hoc inspection.

Every scenario runner (basic, scripted single-turn, scripted multi-turn,
synthetic) calls ``export_transcript`` after loading its history so the
session is captured as a standalone JSON file regardless of whether the
caller passed ``-o``, kept the tempdir, or knows the session id.

Default location: ``<project_root>/target/eval_transcripts/<ts>-<scenario>.json``.
Override with ``OMNIAGENTS_EVAL_TRANSCRIPTS_DIR``.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union


def _safe_name(s: str) -> str:
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in s)


def resolve_project_root(start: Union[str, Path]) -> Path:
    """Resolve the project root from any path inside (or at) it.

    Accepts a file path (e.g. agent.yml) or a directory. Walks upward
    looking for ``project.yml`` / ``project.yaml`` — that's the canonical
    marker. Falls back to the immediate parent if no marker is found.
    """
    start_p = Path(start).resolve()
    current = start_p if start_p.is_dir() else start_p.parent
    fallback = current
    while True:
        if (current / "project.yml").is_file() or (current / "project.yaml").is_file():
            return current
        if current.name == "target":
            return current.parent
        if current.parent == current:
            return fallback
        current = current.parent


def export_transcript(
    history: List[Dict[str, Any]],
    scenario: Dict[str, Any],
    project_root: Path,
    *,
    session_id: Optional[str] = None,
) -> Optional[str]:
    """Write the scenario's loaded history to a per-run JSON file.

    Returns the absolute path written, or ``None`` if there's no history
    (e.g. the agent never produced a session). Failures are swallowed —
    transcript export must never break a run.
    """
    if not history:
        return None

    try:
        out_dir_env = os.environ.get("OMNIAGENTS_EVAL_TRANSCRIPTS_DIR")
        out_dir = (
            Path(out_dir_env).expanduser()
            if out_dir_env
            else (project_root / "target" / "eval_transcripts")
        )
        out_dir.mkdir(parents=True, exist_ok=True)

        name = _safe_name(scenario.get("name") or scenario.get("id") or "scenario")
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]  # ms precision
        out_path = out_dir / f"{ts}-{name}.json"

        payload = {
            "scenario": scenario.get("name"),
            "scenario_id": scenario.get("id"),
            "session_id": session_id,
            "exported_at": datetime.now().isoformat(),
            "history": history,
        }
        out_path.write_text(
            json.dumps(payload, indent=2, default=str), encoding="utf-8"
        )
        return str(out_path)
    except Exception as exc:  # noqa: BLE001
        # Transcript export is best-effort. Surface the failure but don't
        # propagate — a busted disk shouldn't fail an eval run.
        import logging

        logging.getLogger(__name__).warning(
            "transcript export failed for %r: %s", scenario.get("name"), exc
        )
        return None
