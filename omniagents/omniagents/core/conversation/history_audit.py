"""Offline SQLite history-gap audit and conservative repair-to-copy.

Never opens an application store on an original database: those constructors
can migrate schemas. Reports contain counts/row IDs, not conversation text.
Only a fully matched message prefix followed by plain user-message history
rows is repairable. Other mismatches require human review, not text dedupe.
"""

from __future__ import annotations

import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import time

from .adapter import _checkpoint_kind, _message_text


def _read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("BEGIN")
    return connection


def _inspect(history, domain, thread_id):
    report = {
        "thread_id": thread_id,
        "status": "review_required",
        "missing_row_ids": [],
    }
    thread = domain.execute(
        "SELECT * FROM threads WHERE thread_id=?", (thread_id,)
    ).fetchone()
    if thread is None:
        return {
            **report,
            "status": "legacy_only",
            "reason": "No canonical thread; use legacy projection",
        }, []
    if domain.execute(
        "SELECT 1 FROM turns WHERE thread_id=? AND status='running' LIMIT 1",
        (thread_id,),
    ).fetchone():
        return {**report, "status": "active", "reason": "Thread has a running turn"}, []
    rows = history.execute(
        "SELECT id,msg_json FROM history WHERE session_id=? ORDER BY id", (thread_id,)
    ).fetchall()
    items = domain.execute(
        "SELECT * FROM items WHERE thread_id=? ORDER BY seq", (thread_id,)
    ).fetchall()
    try:
        raw = [(row["id"], json.loads(row["msg_json"])) for row in rows]
        if any(not isinstance(row, dict) for _, row in raw):
            raise ValueError("Non-object history")
        messages = [
            (row_id, row)
            for row_id, row in raw
            if (row.get("type") or "message") == "message"
        ]
        canonical = [
            (
                (
                    "compaction",
                    content.get("checkpoint_kind"),
                    content.get("version", 1),
                    content.get("summary"),
                )
                if item["kind"] == "compaction"
                else ("message", item["role"], content.get("text"))
            )
            for item in items
            if item["kind"] in ("user_message", "agent_message", "compaction")
            for content in [json.loads(item["content_json"])]
        ]
        legacy = [
            (
                (
                    "compaction",
                    _checkpoint_kind(row),
                    row["omniagents"].get("version", 1),
                    _message_text(row.get("content")),
                )
                if _checkpoint_kind(row) is not None
                else ("message", row.get("role"), _message_text(row.get("content")))
            )
            for _, row in messages
        ]
    except (ValueError, TypeError, AttributeError):
        return {**report, "reason": "Unreadable or encrypted history/content"}, []
    report.update(
        history_messages=sum(entry[0] == "message" for entry in legacy),
        canonical_messages=sum(entry[0] == "message" for entry in canonical),
        history_checkpoints=sum(entry[0] == "compaction" for entry in legacy),
        canonical_checkpoints=sum(entry[0] == "compaction" for entry in canonical),
        message_sequences_match=[entry for entry in legacy if entry[0] == "message"]
        == [entry for entry in canonical if entry[0] == "message"],
        checkpoint_sequences_match=[
            entry for entry in legacy if entry[0] == "compaction"
        ]
        == [entry for entry in canonical if entry[0] == "compaction"],
    )
    if legacy == canonical:
        return {
            **report,
            "status": "matched",
            "reason": "Message and checkpoint sequences match",
        }, []
    if report["message_sequences_match"] and report["checkpoint_sequences_match"]:
        return {
            **report,
            "reason": "Messages and checkpoints match but their relative ordering differs",
        }, []
    if (
        not canonical
        or legacy[: len(canonical)] != canonical
        or len(legacy) <= len(canonical)
    ):
        return {
            **report,
            "reason": "Not an exact canonical message/checkpoint prefix",
        }, []
    missing = messages[len(canonical) :]
    report["missing_row_ids"] = [row_id for row_id, _ in missing]
    tail = [(row_id, row) for row_id, row in raw if row_id >= missing[0][0]]
    if tail != missing or any(
        row.get("role") != "user"
        or _checkpoint_kind(row) is not None
        or not isinstance(row.get("content"), str)
        or not row["content"]
        for _, row in missing
    ):
        return {
            **report,
            "reason": "Missing suffix is not exclusively plain user messages",
        }, []
    if not items or items[-1]["kind"] not in ("user_message", "agent_message"):
        return {
            **report,
            "reason": "Non-message canonical tail has ambiguous ordering",
        }, []
    return {
        **report,
        "status": "repairable_suffix",
        "reason": "Exact prefix and plain user suffix",
    }, missing


def audit(
    history_path: Path, domain_path: Path, thread_id: str | None = None
) -> list[dict]:
    """Read originals without migrations, materialization or transcript output."""
    with (
        closing(_read_only(history_path)) as history,
        closing(_read_only(domain_path)) as domain,
    ):
        ids = (
            [thread_id]
            if thread_id
            else [
                row[0]
                for row in history.execute(
                    "SELECT DISTINCT session_id FROM history ORDER BY session_id"
                )
            ]
        )
        return [_inspect(history, domain, key)[0] for key in ids]


def repair_copy(
    history_path: Path, domain_path: Path, output: Path, thread_id: str
) -> dict:
    """Repair one unambiguous suffix into a NEW canonical database copy.

    Stop the owning server first. Never installs/replaces the copy. Refuses
    existing output paths, active turns and ambiguous/mismatched sequences.
    Re-auditing a repaired copy reports matched, making reapplication a no-op.
    """
    from .model import Item, ItemKind, ItemStatus, derive_item_id
    from .store import SqliteConversationStore

    with (
        closing(_read_only(history_path)) as history,
        closing(_read_only(domain_path)) as domain,
    ):
        report, missing = _inspect(history, domain, thread_id)
        if report["status"] == "matched":
            return {**report, "copied": False}
        if report["status"] != "repairable_suffix":
            raise ValueError(report["reason"])
        # Exclusive creation also rejects symlinks and either original path.
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)
        with closing(sqlite3.connect(output)) as destination:
            domain.backup(destination)
    store = SqliteConversationStore(path=str(output))
    try:
        thread = store.get_thread(thread_id)
        assert thread is not None
        tail = store.list_items(thread_id, descending=True, limit=1)
        last_seq = max(thread.last_seq, tail[0].seq if tail else 0)
        now = time.time()
        additions = [
            Item(
                thread_id=thread_id,
                item_id=derive_item_id(thread_id, f"history-repair:{row_id}"),
                turn_id=None,
                seq=last_seq + index + 1,
                kind=ItemKind.USER_MESSAGE,
                role="user",
                status=ItemStatus.COMPLETED,
                created_at=now,
                updated_at=now,
                completed_at=now,
                content={"text": row["content"]},
                source_ref={"event": "history_repair", "history_row_id": row_id},
                source="adapter",
            )
            for index, (row_id, row) in enumerate(missing)
        ]
        thread.last_seq = last_seq + len(additions)
        thread.item_count = store.count_items(thread_id) + len(additions)
        # Do not pretend recovery time is the original message time or reorder
        # the user's sidebar: provenance explicitly marks inferred records.
        store.write_projection(thread, [], additions)
    finally:
        store.close()
    return {**report, "copied": True, "repaired_items": len(additions)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", type=Path, required=True)
    parser.add_argument("--domain", type=Path, required=True)
    parser.add_argument("--thread")
    parser.add_argument("--repair-copy", type=Path)
    args = parser.parse_args()
    if args.repair_copy and not args.thread:
        parser.error("--repair-copy requires an explicit --thread")
    result = (
        repair_copy(args.history, args.domain, args.repair_copy, args.thread)
        if args.repair_copy
        else audit(args.history, args.domain, args.thread)
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
