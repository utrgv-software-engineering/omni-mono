"""Domain-model invariants: ids, lifecycle, taxonomy, cursors."""

import pytest

from omniagents.core.conversation.model import (
    CursorError,
    Item,
    ItemKind,
    ItemStatus,
    ToolKind,
    TurnStatus,
    classify_tool,
    decode_cursor,
    derive_item_id,
    derive_legacy_turn_id,
    encode_cursor,
    terminal_status_for_end_reason,
)


def make_item(**overrides):
    base = dict(
        item_id="itm_x",
        thread_id="t1",
        turn_id="run1",
        seq=1,
        kind=ItemKind.TOOL_CALL,
        status=ItemStatus.STARTED,
        created_at=1.0,
        updated_at=1.0,
        content={"tool": "execute_bash"},
    )
    base.update(overrides)
    return Item(**base)


class TestItemIds:
    def test_derivation_is_deterministic(self):
        assert derive_item_id("t1", "tool:c1") == derive_item_id("t1", "tool:c1")

    def test_same_natural_key_in_different_threads_never_collides(self):
        assert derive_item_id("t1", "tool:c1") != derive_item_id("t2", "tool:c1")

    def test_ids_are_prefixed_and_fixed_width(self):
        item_id = derive_item_id("t1", "tool:c1")
        assert item_id.startswith("itm_")
        assert len(item_id) == len("itm_") + 32

    def test_legacy_turn_ids_are_marked_and_stable(self):
        first = derive_legacy_turn_id("t1", 3)
        assert first.startswith("trn_legacy_")
        assert first == derive_legacy_turn_id("t1", 3)
        assert first != derive_legacy_turn_id("t1", 4)


class TestItemLifecycle:
    def test_started_item_advances_to_completed_once(self):
        started = make_item()
        completed = started.with_transition(
            status=ItemStatus.COMPLETED, content={"output": "ok"}, now=2.0
        )
        assert completed.status is ItemStatus.COMPLETED
        assert completed.completed_at == 2.0
        assert completed.revision == 1
        assert completed.content == {"tool": "execute_bash", "output": "ok"}

    def test_terminal_item_refuses_further_transitions(self):
        """The FIRST terminal state is authoritative. A duplicate result —
        replayed, double-delivered, or a retried run reusing a call_id —
        must not rewrite it, or store-only reconstruction is unsound."""
        completed = make_item().with_transition(
            status=ItemStatus.COMPLETED, content={"output": "first"}, now=2.0
        )
        again = completed.with_transition(
            status=ItemStatus.FAILED, content={"output": "second"}, now=3.0
        )
        assert again is completed
        assert again.status is ItemStatus.COMPLETED
        assert again.content["output"] == "first"
        assert again.revision == 1

    @pytest.mark.parametrize(
        "status", [ItemStatus.COMPLETED, ItemStatus.FAILED, ItemStatus.CANCELLED]
    )
    def test_every_terminal_status_is_terminal(self, status):
        item = make_item().with_transition(status=status, now=2.0)
        assert item.is_terminal

    def test_updates_before_terminal_accumulate_revisions(self):
        item = make_item()
        for index in range(3):
            item = item.with_transition(content={"chunk": index}, now=2.0 + index)
        assert item.revision == 3
        assert item.status is ItemStatus.STARTED
        assert item.completed_at is None

    def test_replace_content_mode_drops_prior_fields(self):
        item = make_item().with_transition(
            content={"only": "this"}, now=2.0, merge_content=False
        )
        assert item.content == {"only": "this"}


class TestToolTaxonomy:
    @pytest.mark.parametrize(
        "tool,expected",
        [
            ("execute_bash", ToolKind.COMMAND),
            ("bash_logs", ToolKind.COMMAND),
            ("read_file", ToolKind.FILE),
            ("apply_patch", ToolKind.FILE),
            ("edit_notebook", ToolKind.FILE),
            ("grep_files", ToolKind.SEARCH),
            ("web_search", ToolKind.WEB),
            ("task_create", ToolKind.PLAN),
            ("task_update", ToolKind.PLAN),
            ("display_artifact", ToolKind.ARTIFACT),
            ("spawn_worker", ToolKind.WORKER),
            ("something_unknown", ToolKind.OTHER),
            (None, ToolKind.OTHER),
            ("", ToolKind.OTHER),
        ],
    )
    def test_builtin_tool_names_classify(self, tool, expected):
        assert classify_tool(tool) is expected

    def test_server_label_forces_mcp_even_for_a_builtin_name(self):
        """An MCP server's ``write_file`` is an MCP call, not a file edit."""
        assert classify_tool("write_file", server_label="docs") is ToolKind.MCP

    def test_flat_mcp_wire_prefix_is_the_fallback_signal(self):
        assert classify_tool("mcp_docs__search") is ToolKind.MCP
        assert classify_tool("mcp_notprefixed") is ToolKind.OTHER


class TestEndReasonMapping:
    @pytest.mark.parametrize(
        "reason,expected",
        [
            ("completed", TurnStatus.COMPLETED),
            ("error", TurnStatus.FAILED),
            ("cancelled", TurnStatus.CANCELLED),
            ("stopped", TurnStatus.CANCELLED),
            ("timeout", TurnStatus.FAILED),
            (None, TurnStatus.COMPLETED),
            ("something_new", TurnStatus.COMPLETED),
        ],
    )
    def test_end_reason_maps_to_a_terminal_turn_status(self, reason, expected):
        assert terminal_status_for_end_reason(reason) is expected


class TestCursors:
    def test_roundtrip(self):
        assert decode_cursor(encode_cursor({"seq": 42})) == {"seq": 42}

    def test_cursor_is_opaque_and_url_safe(self):
        cursor = encode_cursor({"seq": 42})
        assert "seq" not in cursor
        assert "=" not in cursor
        assert all(ch.isalnum() or ch in "-_" for ch in cursor)

    @pytest.mark.parametrize("bad", ["!!!!", "bm90LWpzb24", "WzFd"])
    def test_malformed_cursors_raise(self, bad):
        with pytest.raises(CursorError):
            decode_cursor(bad)
