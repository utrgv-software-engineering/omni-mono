"""Tests for omniagents.core.utils.prompt_files."""

from __future__ import annotations

from pathlib import Path

from omniagents.core.utils.prompt_files import (
    DEFAULT_MAX_PROMPT_FILE_BYTES,
    resolve_prompt_file,
)

FALLBACK = "builtin fallback prompt"


class TestCandidateOrder:
    def test_first_candidate_wins(self, tmp_path: Path):
        first = tmp_path / "workspace" / "loop.md"
        second = tmp_path / "config" / "loop.md"
        first.parent.mkdir()
        second.parent.mkdir()
        first.write_text("workspace prompt")
        second.write_text("user config prompt")
        assert resolve_prompt_file([first, second], FALLBACK) == "workspace prompt"

    def test_missing_first_falls_through_to_second(self, tmp_path: Path):
        first = tmp_path / "workspace" / "loop.md"  # never created
        second = tmp_path / "config" / "loop.md"
        second.parent.mkdir()
        second.write_text("user config prompt")
        assert resolve_prompt_file([first, second], FALLBACK) == "user config prompt"

    def test_no_candidates_returns_fallback(self, tmp_path: Path):
        assert resolve_prompt_file([], FALLBACK) == FALLBACK
        assert resolve_prompt_file([tmp_path / "nope.md"], FALLBACK) == FALLBACK

    def test_empty_or_whitespace_file_is_skipped(self, tmp_path: Path):
        empty = tmp_path / "empty.md"
        empty.write_text("   \n\t\n")
        real = tmp_path / "real.md"
        real.write_text("actual prompt")
        assert resolve_prompt_file([empty, real], FALLBACK) == "actual prompt"
        assert resolve_prompt_file([empty], FALLBACK) == FALLBACK

    def test_directory_candidate_is_skipped(self, tmp_path: Path):
        d = tmp_path / "loop.md"
        d.mkdir()
        assert resolve_prompt_file([d], FALLBACK) == FALLBACK

    def test_content_is_stripped(self, tmp_path: Path):
        p = tmp_path / "loop.md"
        p.write_text("\n\n  prompt body  \n\n")
        assert resolve_prompt_file([p], FALLBACK) == "prompt body"


class TestSizeCap:
    def test_oversized_file_is_truncated_not_rejected(self, tmp_path: Path):
        p = tmp_path / "big.md"
        p.write_text("x" * 100)
        result = resolve_prompt_file([p], FALLBACK, max_bytes=10)
        assert result == "x" * 10

    def test_default_cap(self, tmp_path: Path):
        p = tmp_path / "big.md"
        p.write_text("y" * (DEFAULT_MAX_PROMPT_FILE_BYTES + 5_000))
        result = resolve_prompt_file([p], FALLBACK)
        assert len(result) == DEFAULT_MAX_PROMPT_FILE_BYTES

    def test_undecodable_bytes_are_replaced(self, tmp_path: Path):
        p = tmp_path / "binary.md"
        p.write_bytes(b"prompt \xff\xfe text")
        result = resolve_prompt_file([p], FALLBACK)
        assert result.startswith("prompt ")
        assert result.endswith(" text")
