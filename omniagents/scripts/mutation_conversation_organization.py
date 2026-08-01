"""Mutation harness for the conversation organization surface.

Checked in so the "N mutants, 0 survivors" claim on tkt_eZldW-Gje6kn is
reproducible rather than asserted. Each entry deletes ONE substantive
behaviour by textual substitution, runs the tests that should defend it,
restores the file, and reports whether the mutant was killed.

The mutations are chosen by walking the INVARIANTS the surface claims —
protocol.md § "Conversation Organization, Search & Branching" and the
policy paragraphs — not by walking the lines the change touched. Two
review rounds found that a line-shaped list has blind spots exactly where
a claim is made but never exercised (the tokenization contract, the
live-state indicators, the ordering of a cascade).

Usage::

    python scripts/mutation_conversation_organization.py            # all
    python scripts/mutation_conversation_organization.py search     # subset

Postgres mutants are skipped unless ``OMNIAGENTS_TEST_PG_URL`` is set.
Exit status is non-zero if any mutant survived.
"""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import List, NamedTuple, Sequence

ROOT = Path(__file__).resolve().parents[1]

STORE = "omniagents/core/conversation/store.py"
MODEL = "omniagents/core/conversation/model.py"
RECORDER = "omniagents/core/conversation/recorder.py"
SERVICE = "omniagents/core/conversation/service.py"
AGENT = "omniagents/core/agents/service.py"
RETENTION = "omniagents/core/session/retention.py"
ADAPTER = "omniagents/core/conversation/adapter.py"

UNIT = "tests/unit/core/conversation"
STORE_TESTS = f"{UNIT}/test_store.py"
BRANCH_TESTS = f"{UNIT}/test_branching.py"
PG_TESTS = f"{UNIT}/test_postgres_store.py"
INTEG = "tests/integration/test_server_conversation_organization.py"


class Mutation(NamedTuple):
    #: The invariant this mutant attacks, in the words protocol.md uses.
    invariant: str
    path: str
    old: str
    new: str
    targets: Sequence[str]
    needs_pg: bool = False


MUTATIONS: List[Mutation] = [
    # -- Branching: the child is a prefix of the parent -----------------
    Mutation(
        "fork: the child holds the parent's items UP TO the branch point",
        RECORDER,
        "before_seq=None if up_to_seq is None else up_to_seq + 1,",
        "before_seq=None,",
        [BRANCH_TESTS, INTEG],
    ),
    Mutation(
        "fork: the child's last_seq starts above its own copied rows",
        RECORDER,
        "child.last_seq = max(item.seq for item in copied_items)",
        "child.last_seq = parent.last_seq",
        [BRANCH_TESTS],
    ),
    Mutation(
        "fork: a turn with no copied items is not copied",
        RECORDER,
        "if turn.turn_id in items_by_turn",
        "if True",
        [BRANCH_TESTS, INTEG],
    ),
    Mutation(
        "fork: a copied turn's counters describe what landed",
        RECORDER,
        """            item_count=len(seqs),
            first_seq=min(seqs) if seqs else None,
            last_seq=max(seqs) if seqs else None,""",
        """            item_count=turn.item_count,
            first_seq=turn.first_seq,
            last_seq=turn.last_seq,""",
        [BRANCH_TESTS],
    ),
    Mutation(
        "fork: a compaction floor above the branch is not inherited",
        RECORDER,
        """        checkpoint_seq = compaction.get("checkpoint_seq")
        if isinstance(checkpoint_seq, int) and checkpoint_seq > last_seq:
            return {}""",
        "",
        [BRANCH_TESTS],
    ),
    Mutation(
        "fork: the branch point travels on the session_forked notification",
        AGENT,
        '            payload["from_item_id"] = branch_item.item_id',
        "            pass",
        [BRANCH_TESTS, INTEG],
    ),
    # -- Branching: context matches transcript --------------------------
    Mutation(
        "fork: the child's raw history is cut with its transcript",
        AGENT,
        """                history = truncate_history_at_user_row(
                    sess.history,
                    user_rows,
                    include_next_row=include_next,
                )""",
        "                history = None",
        [BRANCH_TESTS],
    ),
    Mutation(
        "fork: the cut counts USER ROWS, not turns",
        SERVICE,
        """        seen = self._user_rows_through(thread_id, item.seq)""",
        """        seen = sum(
            1
            for turn in self.store.list_turns(thread_id, limit=MAX_PAGE_SIZE * 10)
            if turn.first_seq is not None and turn.first_seq <= item.seq
        )""",
        [BRANCH_TESTS],
    ),
    Mutation(
        "fork: branching AT a question excludes its answer",
        SERVICE,
        """        if item.kind is ItemKind.USER_MESSAGE:
            # Keep everything before this question, plus the question.
            return seen - 1, True
        return seen, False""",
        "        return seen, False",
        [BRANCH_TESTS],
    ),
    Mutation(
        "fork: a position with no exact cut is refused, not approximated",
        SERVICE,
        """        segment_tail, blocking = self._segment_tail_after(thread_id, item.seq)
        if segment_tail:
            return""",
        """        segment_tail, blocking = self._segment_tail_after(thread_id, item.seq)
        if True:
            return""",
        [BRANCH_TESTS],
    ),
    Mutation(
        "fork: an item with no history row does not block a cut",
        SERVICE,
        "                if row.kind in _HISTORY_BEARING_KINDS:",
        "                if True:",
        [BRANCH_TESTS],
    ),
    Mutation(
        "fork: a running turn is not a fixed branch point",
        SERVICE,
        "            if turn is not None and turn.status is TurnStatus.RUNNING:",
        "            if False:",
        [BRANCH_TESTS],
    ),
    Mutation(
        "fork: at most one fork-point parameter",
        SERVICE,
        "        if from_item_id and from_turn_id:",
        "        if False:",
        [BRANCH_TESTS],
    ),
    # -- Descendant policy ----------------------------------------------
    Mutation(
        "delete: a thread with descendants is refused, never orphaned",
        AGENT,
        "        if descendant_ids and not cascade:",
        "        if False:",
        [BRANCH_TESTS, INTEG],
    ),
    Mutation(
        # ``descendant_owners`` is the ONE walk both cascades ride, so this
        # covers the archive path and the delete path at their source.
        "cascade: the walk is UNBOUNDED, unlike the ancestry read",
        SERVICE,
        "        rows = self.store.descendant_thread_rows(thread_id)",
        """        rows = [
            (child.thread_id, child.user_id, child.parent_thread_id)
            for child, _ in self.store.list_descendant_threads(
                thread_id, max_depth=MAX_DESCENDANT_DEPTH, limit=MAX_DESCENDANTS
            )
        ]""",
        [BRANCH_TESTS, INTEG],
    ),
    Mutation(
        "delete: the guard walk is UNBOUNDED, unlike the ancestry read",
        AGENT,
        "            descendants = self._conversation_service().descendant_nodes(session_id)",
        """            descendants = [
                node
                for node in self._conversation_service().descendant_nodes(session_id)
                if node.depth <= 32
            ][:500]""",
        [BRANCH_TESTS],
    ),
    Mutation(
        "delete: the guard sees descendants it may not READ",
        STORE,
        """                SELECT thread_id FROM threads
                 WHERE parent_thread_id = ? AND thread_id <> ?""",
        """                SELECT thread_id FROM threads
                 WHERE parent_thread_id = ? AND thread_id <> ?
                   AND user_id IS (SELECT user_id FROM threads WHERE thread_id = ?)""",
        [BRANCH_TESTS, STORE_TESTS],
    ),
    Mutation(
        "delete: a truncated id list is marked truncated",
        SERVICE,
        "        truncated = total > len(listed)",
        "        truncated = False",
        [BRANCH_TESTS],
    ),
    Mutation(
        "delete: the reported descendant count is exact",
        SERVICE,
        "            descendant_count=total,",
        "            descendant_count=len(listed),",
        [BRANCH_TESTS],
    ),
    Mutation(
        "delete: a cascade does not remove another principal's thread",
        AGENT,
        """            if foreign:
                raise AuthorizationDenied(""",
        """            if False:
                raise AuthorizationDenied(""",
        [INTEG],
    ),
    Mutation(
        "delete: the cascade reaches the descendants",
        AGENT,
        "        for child in descendant_ids:",
        "        for child in []:",
        [BRANCH_TESTS, INTEG],
    ),
    Mutation(
        "delete: the cascade removes deepest first",
        SERVICE,
        "        nodes.sort(key=lambda node: (-node.depth, node.thread_id))",
        "        nodes.sort(key=lambda node: (node.depth, node.thread_id))",
        [BRANCH_TESTS],
    ),
    Mutation(
        "delete: legal hold is checked BEFORE anything is deleted",
        AGENT,
        """        held = self._sessions_on_legal_hold([session_id, *descendant_ids])
        if held:""",
        """        held = self._sessions_on_legal_hold([session_id, *descendant_ids])
        if False:""",
        [BRANCH_TESTS, INTEG],
    ),
    Mutation(
        "delete: a hold lookup that cannot answer counts as held",
        AGENT,
        "                on_hold = True",
        "                on_hold = False",
        [BRANCH_TESTS],
    ),
    Mutation(
        "archive: cascades to the fork subtree",
        SERVICE,
        "        for node in self.descendant_nodes(thread_id):",
        "        for node in []:",
        [BRANCH_TESTS, INTEG],
    ),
    Mutation(
        "archive: the cascade skips descendants the caller cannot access",
        SERVICE,
        """            if not can_access(node.user_id):
                skipped += 1
                continue""",
        "            pass",
        [BRANCH_TESTS, INTEG],
    ),
    Mutation(
        "archive: archive_session scopes its cascade too (the STABLE path)",
        AGENT,
        "                can_access=self._descendant_access_predicate(),",
        "                can_access=lambda owner: True,",
        [INTEG],
    ),
    Mutation(
        "delete: a refusal does not name a thread the caller cannot read",
        AGENT,
        "        visible = [node.thread_id for node in descendants if can_access(node.user_id)]",
        "        visible = list(descendant_ids)",
        [INTEG],
    ),
    Mutation(
        "delete: a -32014 does not name a foreign descendant either",
        AGENT,
        """                    # The NAMED thread, never a foreign descendant's id.
                    resource_id=session_id,""",
        "                    resource_id=descendant_ids[0],",
        [INTEG],
    ),
    Mutation(
        "fork: the -32112 payload names what blocked the cut",
        SERVICE,
        """            next_item_id=blocking.item_id if blocking is not None else None,
            next_item_kind=blocking.kind.value if blocking is not None else None,""",
        "            next_item_id=None,",
        [BRANCH_TESTS],
    ),
    Mutation(
        "retention: the FTS tombstones are reclaimed, not just the rows",
        STORE,
        """            for table in ("item_search", "thread_search"):
                try:
                    conn.execute(f"INSERT INTO {table}({table}) VALUES('optimize')")
                except sqlite3.OperationalError:  # pragma: no cover - defensive
                    logger.exception("FTS optimize failed for %s", table)""",
        "            pass",
        [BRANCH_TESTS],
    ),
    Mutation(
        "retention: delete_session compacts the index once per operation",
        AGENT,
        """        compact_conversation_index(
            project_slug=getattr(self._session_manager, "_project_slug", None),
            agent_slug=getattr(self._session_manager, "_agent_slug", None),
        )
        return deleted""",
        "        return deleted",
        [BRANCH_TESTS],
    ),
    Mutation(
        "retention: purge_threads compacts the index too",
        RETENTION,
        """    if purged:
        compact_conversation_index(project_slug=project_slug, agent_slug=agent_slug)""",
        "    pass",
        [BRANCH_TESTS],
    ),
    Mutation(
        "export: a clipped turn-header list says so",
        SERVICE,
        """            "turns_truncated": (
                position is None and self.store.count_turns(thread_id) > len(turns)
            ),""",
        '            "turns_truncated": False,',
        [BRANCH_TESTS],
    ),
    Mutation(
        "archive: the legacy sessions.archived flag is kept in step",
        AGENT,
        """            cascaded = self._cascade_archive(session_id, True)
            for target in cascaded:
                self._mirror_archive_flag(target, True)""",
        "            cascaded = self._cascade_archive(session_id, True)",
        [BRANCH_TESTS],
    ),
    # -- Metadata + notification ----------------------------------------
    Mutation(
        "metadata: a change is observable on other connections",
        AGENT,
        """        if result["changed"]:
            await self._broadcast_thread_updated(""",
        """        if False:
            await self._broadcast_thread_updated(""",
        [INTEG],
    ),
    Mutation(
        "metadata: archive_session is observable too (the STABLE path)",
        AGENT,
        """            if thread is not None:
                await self._broadcast_thread_updated(
                    thread, ["status"], [t for t in cascaded if t != session_id]
                )""",
        "            del thread",
        [INTEG],
    ),
    Mutation(
        "metadata: the notification is filtered per recipient",
        AGENT,
        '            if not policy.can_access(getattr(ch, "principal", None), owner):\n                continue',
        "            pass",
        [INTEG],
    ),
    Mutation(
        "metadata: merges, and a null value removes one key",
        SERVICE,
        """            merged = dict(thread.metadata)
            for key, value in metadata.items():
                if value is None:
                    merged.pop(key, None)
                else:
                    merged[key] = value""",
        "            merged = dict(metadata)",
        [BRANCH_TESTS],
    ),
    Mutation(
        "metadata: an omitted title is not a clear",
        SERVICE,
        "        if not isinstance(title, _Unset):",
        "        if True:",
        [BRANCH_TESTS, INTEG],
    ),
    Mutation(
        "metadata: the size cap applies to the MERGED record",
        SERVICE,
        "            _validate_metadata(merged)",
        "            pass",
        [BRANCH_TESTS, INTEG],
    ),
    # -- Listing --------------------------------------------------------
    Mutation(
        "listing: the ownership scope is part of the SQL predicate",
        STORE,
        """        if thread_filter.user_id is not UNSCOPED:
            # ``user_id = ?`` never matches a NULL owner, which is the
            # intended behaviour: an unowned thread is not accessible to a
            # named principal (see rpc.auth.principal_can_access).
            clauses.append("threads.user_id = ?")
            params.append(thread_filter.user_id)""",
        "",
        [STORE_TESTS, INTEG],
    ),
    Mutation(
        "listing[pg]: the ownership scope is part of the SQL predicate",
        STORE,
        """        if thread_filter.user_id is not UNSCOPED:
            clauses.append("conversation_threads.user_id = %s")
            params.append(thread_filter.user_id)""",
        "",
        [PG_TESTS],
        needs_pg=True,
    ),
    Mutation(
        "listing: the keyset is total, so a page boundary never skips",
        STORE,
        """            clauses.append(
                f"(threads.updated_at {comparison} ? OR "
                f"(threads.updated_at = ? AND threads.thread_id {comparison} ?))"
            )
            params.extend([float(sort_value), float(sort_value), thread_id])""",
        """            clauses.append(f"threads.updated_at {comparison} ?")
            params.append(float(sort_value))""",
        [STORE_TESTS],
    ),
    Mutation(
        "listing: total is counted under the page's own predicate",
        STORE,
        """        clauses, params = self._thread_predicate(thread_filter)
        with self._lock:
            row = (
                self._connect()
                .execute(
                    f"SELECT COUNT(*) FROM threads WHERE {' AND '.join(clauses)}",""",
        """        clauses, params = self._thread_predicate(ThreadFilter(UNSCOPED))
        with self._lock:
            row = (
                self._connect()
                .execute(
                    f"SELECT COUNT(*) FROM threads WHERE {' AND '.join(clauses)}",""",
        [STORE_TESTS, INTEG],
    ),
    Mutation(
        "listing: the model filter matches through the thread's turns",
        STORE,
        """        if thread_filter.model is not None:
            # The model is a property of a Turn, not of a Thread, and
            # denormalizing it onto the thread row would add a write-path
            # field that can go stale. An EXISTS keeps one source of truth.
            clauses.append(
                "EXISTS (SELECT 1 FROM turns WHERE turns.thread_id = "
                "threads.thread_id AND turns.model = ?)"
            )
            params.append(thread_filter.model)""",
        "",
        [STORE_TESTS],
    ),
    Mutation(
        "listing: counts are counted at read time, not remembered",
        SERVICE,
        """            payload["item_count"] = item_counts.get(thread.thread_id, 0)
            payload["turn_count"] = turn_counts.get(thread.thread_id, 0)""",
        "            pass",
        [INTEG, STORE_TESTS],
    ),
    # -- Live state (named ticket scope) --------------------------------
    Mutation(
        "listing: pending-attention counts the prompts actually waiting",
        AGENT,
        """        attention = {
            "tool_approvals": len(getattr(sess, "pending_tool_approvals", {}) or {}),
            "mcp_approvals": len(getattr(sess, "pending_mcp_approvals", {}) or {}),
            "elicitations": len(getattr(sess, "pending_elicitations", {}) or {}),
            "client_requests": len(getattr(sess, "pending_client_requests", {}) or {}),
        }""",
        """        attention = {
            "tool_approvals": 0,
            "mcp_approvals": 0,
            "elicitations": 0,
            "client_requests": 0,
        }""",
        [INTEG],
    ),
    Mutation(
        "listing: `active` reflects the live task",
        AGENT,
        '            "active": task is not None and not task.done(),',
        '            "active": False,',
        [INTEG],
    ),
    Mutation(
        "listing: `loaded` is false for a thread with no live session",
        AGENT,
        """            return {
                "loaded": False,""",
        """            return {
                "loaded": True,""",
        [INTEG],
    ),
    # -- Search ---------------------------------------------------------
    Mutation(
        "search: the index tracks a revised item",
        STORE,
        """    CREATE TRIGGER IF NOT EXISTS item_search_au AFTER UPDATE ON items BEGIN
        DELETE FROM item_search WHERE rowid = old.rowid;
        INSERT INTO item_search(rowid, thread_id, item_id, turn_id, kind, seq, text)
        VALUES (new.rowid, new.thread_id, new.item_id, new.turn_id, new.kind,
                new.seq, COALESCE(new.search_text, ''));
    END
    \"\"\",""",
        """    CREATE TRIGGER IF NOT EXISTS item_search_au AFTER UPDATE ON items BEGIN
        SELECT 1;
    END
    \"\"\",""",
        [STORE_TESTS],
    ),
    Mutation(
        "search: the index does not outlive the transcript",
        STORE,
        """    CREATE TRIGGER IF NOT EXISTS item_search_ad AFTER DELETE ON items BEGIN
        DELETE FROM item_search WHERE rowid = old.rowid;
    END
    \"\"\",""",
        """    CREATE TRIGGER IF NOT EXISTS item_search_ad AFTER DELETE ON items BEGIN
        SELECT 1;
    END
    \"\"\",""",
        [STORE_TESTS],
    ),
    Mutation(
        "search: titles are findable",
        STORE,
        """    CREATE TRIGGER IF NOT EXISTS thread_search_ai AFTER INSERT ON threads BEGIN
        INSERT INTO thread_search(rowid, thread_id, text)
        VALUES (new.rowid, new.thread_id, COALESCE(new.title, ''));
    END
    \"\"\",""",
        """    CREATE TRIGGER IF NOT EXISTS thread_search_ai AFTER INSERT ON threads BEGIN
        SELECT 1;
    END
    \"\"\",""",
        [STORE_TESTS],
    ),
    Mutation(
        "search: a bulk projection write is indexed",
        STORE,
        """    CREATE TRIGGER IF NOT EXISTS item_search_ai AFTER INSERT ON items BEGIN
        INSERT INTO item_search(rowid, thread_id, item_id, turn_id, kind, seq, text)
        VALUES (new.rowid, new.thread_id, new.item_id, new.turn_id, new.kind,
                new.seq, COALESCE(new.search_text, ''));
    END
    \"\"\",""",
        """    CREATE TRIGGER IF NOT EXISTS item_search_ai AFTER INSERT ON items BEGIN
        SELECT 1;
    END
    \"\"\",""",
        [STORE_TESTS],
    ),
    Mutation(
        "search: a match is scoped to the caller in SQL",
        STORE,
        "                  JOIN threads ON threads.thread_id = m.thread_id\n"
        "                 WHERE {' AND '.join(clauses)}",
        "                  JOIN threads ON threads.thread_id = m.thread_id",
        [STORE_TESTS, INTEG],
    ),
    Mutation(
        "search: tool payloads are indexed, not only messages",
        MODEL,
        """_SEARCHABLE_CONTENT_KEYS = (
    "title",
    "text",
    "summary",
    "message",
    "tool",
    "server_label",
    "input",
    "arguments",
    "output",
    "value",
    "reason",
    "rejection_message",
)""",
        '_SEARCHABLE_CONTENT_KEYS = ("title", "text", "summary", "message")',
        [STORE_TESTS, INTEG],
    ),
    Mutation(
        "search: an elicitation ANSWER is never indexed or previewed",
        MODEL,
        """_UNINDEXED_CONTENT_KEYS: Dict[str, frozenset] = {
    "elicitation": frozenset({"value"}),
}""",
        "_UNINDEXED_CONTENT_KEYS: Dict[str, frozenset] = {}",
        [STORE_TESTS],
    ),
    Mutation(
        "search: the query is a bag of terms, ANDed",
        STORE,
        "        match = \" \".join('\"' + term.replace('\"', '\"\"') + '\"' for term in terms)",
        "        match = '\"' + terms[0].replace('\"', '\"\"') + '\"'",
        [STORE_TESTS],
    ),
    Mutation(
        "search: the query is split the way the index splits the document",
        MODEL,
        """    terms: List[str] = []
    current: List[str] = []
    for char in query:
        if char.isalnum():
            current.append(char)
        elif current:
            terms.append("".join(current))
            current = []
    if current:
        terms.append("".join(current))
    return terms""",
        """    stripped = query.strip()
    return [stripped] if stripped else []""",
        [STORE_TESTS],
    ),
    Mutation(
        # Killable only because the PG parity test bootstraps a THROWAWAY
        # schema: ``ADD COLUMN IF NOT EXISTS`` is a no-op against a table
        # that already exists, so a check run on a deployed database cannot
        # see a change to this expression at all. (Which is also why
        # changing it needs a migration — see the PR's follow-ups.)
        "search[pg]: the tsvector is normalized to the same token stream",
        STORE,
        "            regexp_replace(coalesce(search_text, ''), '[^[:alnum:]]+', ' ', 'g')",
        "            coalesce(search_text, '')",
        [PG_TESTS],
        needs_pg=True,
    ),
    Mutation(
        "search[pg]: pre-upgrade history is backfilled and searchable",
        STORE,
        "        self._backfill_search_text()",
        "        pass",
        [PG_TESTS],
        needs_pg=True,
    ),
    # -- Ancestry -------------------------------------------------------
    Mutation(
        # tkt_YF3ocr6GxnXa changed this invariant's SHAPE, not its force.
        # It used to be "the read stays inside the root's owner", enforced
        # transitively in SQL; the read now descends past a foreign node
        # and omits only that node. Both halves get a mutant.
        "ancestry: an ancestry READ omits a node the caller cannot access",
        SERVICE,
        "            and can_access(node.user_id)",
        "            and True",
        [BRANCH_TESTS, INTEG],
    ),
    Mutation(
        "ancestry: an ancestry READ descends PAST a node it omits",
        SERVICE,
        """        visible = [
            node
            for node in self.descendant_nodes(thread_id)
            if (max_depth is None or node.depth <= max_depth)
            and can_access(node.user_id)
        ]""",
        """        _reachable: set[str] = set()
        _parents = {
            child: parent
            for child, _, parent in self.store.descendant_thread_rows(thread_id)
        }
        for _node in sorted(self.descendant_nodes(thread_id), key=lambda n: n.depth):
            _parent = _parents.get(_node.thread_id)
            if _parent in _parents and _parent not in _reachable:
                continue
            if can_access(_node.user_id):
                _reachable.add(_node.thread_id)
        visible = [
            node
            for node in self.descendant_nodes(thread_id)
            if (max_depth is None or node.depth <= max_depth)
            and node.thread_id in _reachable
        ]""",
        [BRANCH_TESTS],
    ),
    Mutation(
        "ancestry: an ancestry READ respects its depth ceiling",
        SERVICE,
        "        rows = [node for node in accessible if node.depth <= depth]",
        "        rows = list(accessible)",
        [BRANCH_TESTS, INTEG],
    ),
    Mutation(
        # The clip is only honest if the payload admits it: the cascade
        # this read predicts is uncapped, so a silent clip reintroduces
        # tkt_YF3ocr6GxnXa one axis over.
        "ancestry: a clipped ancestry READ says so",
        SERVICE,
        "        truncated = len(accessible) > len(page)",
        "        truncated = len(rows) > len(page)",
        [BRANCH_TESTS],
    ),
    Mutation(
        "ancestry: the shared walk is derived once per node, not per query",
        SERVICE,
        "                if cursor in depths:",
        "                if False:",
        [BRANCH_TESTS],
    ),
    # -- Retention ------------------------------------------------------
    Mutation(
        "retention: the domain rows go with the history",
        RETENTION,
        "        get_conversation_store(project_slug, agent_slug).delete_thread(session_id)",
        "        pass",
        [BRANCH_TESTS],
    ),
    Mutation(
        "retention: the replay journal goes with the history",
        RETENTION,
        """        SessionEventLog(
            session_id, project_slug=project_slug, agent_slug=agent_slug
        ).destroy()""",
        "        pass",
        [BRANCH_TESTS],
    ),
    Mutation(
        "retention: legal hold wins over the retention window",
        AGENT,
        """                and not history_db.get_session_hold(
                    row["id"],
                    project_slug=project_slug,
                    agent_slug=agent_slug,
                )""",
        "",
        [BRANCH_TESTS],
    ),
    Mutation(
        "retention: an undecodable timestamp reads as recent, not ancient",
        AGENT,
        '        except ValueError:\n            return float("inf")',
        "        except ValueError:\n            return 0.0",
        [BRANCH_TESTS],
    ),
    # -- Export ---------------------------------------------------------
    Mutation(
        "export: turn structure ships with the first page",
        SERVICE,
        """        turns = (
            self.store.list_turns(thread_id, limit=MAX_PAGE_SIZE)
            if position is None
            else []
        )""",
        "        turns = []",
        [BRANCH_TESTS],
    ),
    Mutation(
        "export: descendants are enumerated on request",
        SERVICE,
        """            payload["descendant_thread_ids"] = [
                node.thread_id
                for node in self.accessible_descendants(
                    thread_id, can_access=can_access, max_depth=MAX_DESCENDANT_DEPTH
                )[:MAX_DESCENDANTS]
            ]""",
        '            payload["descendant_thread_ids"] = []',
        [BRANCH_TESTS],
    ),
    # -- Compatibility --------------------------------------------------
    Mutation(
        "fork: a thread with NO user rows keeps its whole context",
        ADAPTER,
        """    # No early return for ``user_row_count == 0``: a thread whose turns were
    # all opened by assistant-role wakeups has NO user rows at all, and for
    # it "keep everything up to the 1st user row" means keep everything.
    # Short-circuiting to [] would hand such a fork an empty context.
    seen = 0""",
        """    if user_row_count <= 0 and not include_next_row:
        return []
    seen = 0""",
        [BRANCH_TESTS],
    ),
    Mutation(
        "adapter: the projection's turn boundary is a user row",
        ADAPTER,
        '    return (raw.get("type") or "message") == "message" '
        'and raw.get("role") == "user"',
        '    return raw.get("role") == "assistant"',
        [BRANCH_TESTS, f"{UNIT}/test_adapter.py"],
    ),
]


def _run(targets: Sequence[str], needs_pg: bool) -> bool:
    env = dict(os.environ)
    env["PATH"] = f"{ROOT / '.venv' / 'bin'}:{env['PATH']}"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-x",
            "-q",
            "--timeout=180",
            *targets,
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    _ = needs_pg
    return result.returncode == 0


def main() -> int:
    only = sys.argv[1] if len(sys.argv) > 1 else None
    has_pg = bool(os.getenv("OMNIAGENTS_TEST_PG_URL"))
    survivors: List[str] = []
    skipped = 0
    ran = 0

    for mutation in MUTATIONS:
        if only and only not in mutation.invariant:
            continue
        if mutation.needs_pg and not has_pg:
            skipped += 1
            print(f"-- skipped (no PG)  {mutation.invariant}")
            continue
        path = ROOT / mutation.path
        original = path.read_text()
        occurrences = original.count(mutation.old)
        if occurrences != 1:
            survivors.append(f"ANCHOR x{occurrences}: {mutation.invariant}")
            print(f"!! anchor matched {occurrences}x  {mutation.invariant}")
            continue
        path.write_text(original.replace(mutation.old, mutation.new, 1))
        try:
            ast.parse(path.read_text())
            killed = not _run(mutation.targets, mutation.needs_pg)
        finally:
            path.write_text(original)
        ran += 1
        if not killed:
            survivors.append(mutation.invariant)
        print(
            f"{'!!' if not killed else 'ok'} "
            f"{'SURVIVED' if not killed else 'killed  '} {mutation.invariant}"
        )

    print(
        json.dumps({"ran": ran, "skipped_no_pg": skipped, "survivors": len(survivors)})
    )
    for name in survivors:
        print(f"SURVIVOR: {name}")
    return 1 if survivors else 0


if __name__ == "__main__":
    raise SystemExit(main())
