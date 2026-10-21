"""Assign legacy (unowned) sessions to a principal.

Sessions created before authentication was enabled have ``user_id = NULL``.
Under enforced authorization an unowned session is inaccessible: an absent
owner is not consent, and auto-claiming one on first access would both
expose it to whoever asked first and lock out its real creator (see
:func:`omniagents.rpc.auth.principal_can_access`).

This module is the explicit migration path. Run it once, deliberately,
naming the principal that should own the pre-auth history::

    python -m omniagents.core.session.backfill --user-id alice
    python -m omniagents.core.session.backfill --user-id alice --dry-run

Sessions that already have an owner are never touched.
"""

from __future__ import annotations

import argparse
from typing import List

from omniagents.core.session.history_db import (
    get_session_user_id,
    list_sessions,
    update_session_user_id,
)

__all__ = ["find_unowned_sessions", "backfill_session_owners", "main"]


def find_unowned_sessions(
    *,
    project_slug: str | None = None,
    agent_slug: str | None = None,
) -> List[str]:
    """Session ids with no recorded owner."""
    unowned: List[str] = []
    for row in list_sessions(project_slug=project_slug, agent_slug=agent_slug):
        session_id = row.get("id")
        if not isinstance(session_id, str) or not session_id:
            continue
        owner = row.get("user_id", "__missing__")
        if owner == "__missing__":
            # Backend didn't include the column — ask directly.
            owner = get_session_user_id(
                session_id, project_slug=project_slug, agent_slug=agent_slug
            )
        if owner is None:
            unowned.append(session_id)
    return unowned


def backfill_session_owners(
    user_id: str,
    *,
    project_slug: str | None = None,
    agent_slug: str | None = None,
    dry_run: bool = False,
) -> List[str]:
    """Assign every unowned session to *user_id*.

    Returns the session ids that were (or, with *dry_run*, would be)
    assigned. Sessions with an existing owner are left alone.
    """
    if not user_id:
        raise ValueError("user_id is required")
    targets = find_unowned_sessions(project_slug=project_slug, agent_slug=agent_slug)
    if dry_run:
        return targets
    for session_id in targets:
        update_session_user_id(
            session_id,
            user_id,
            project_slug=project_slug,
            agent_slug=agent_slug,
        )
    return targets


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Assign legacy unowned sessions to a principal."
    )
    parser.add_argument("--user-id", required=True, help="Owner to assign")
    parser.add_argument("--project-slug", default=None)
    parser.add_argument("--agent-slug", default=None)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing",
    )
    args = parser.parse_args(argv)
    assigned = backfill_session_owners(
        args.user_id,
        project_slug=args.project_slug,
        agent_slug=args.agent_slug,
        dry_run=args.dry_run,
    )
    verb = "would assign" if args.dry_run else "assigned"
    print(f"{verb} {len(assigned)} unowned session(s) to {args.user_id}")
    for session_id in assigned:
        print(f"  {session_id}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
