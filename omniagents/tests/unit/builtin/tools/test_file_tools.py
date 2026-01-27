import importlib
import re
import sys
import types

if "pygit2" not in sys.modules:
    fake_pygit2 = types.ModuleType("pygit2")

    class _StubRepo:
        def __init__(self, gitdir):
            self.workdir = gitdir

        def path_is_ignored(self, path):
            return False

        def status_file(self, rel):
            return 0

    fake_pygit2.discover_repository = lambda start_dir=None: None
    fake_pygit2.Repository = _StubRepo
    fake_pygit2.GIT_STATUS_IGNORED = 0x1000
    sys.modules["pygit2"] = fake_pygit2

if "bs4" not in sys.modules:
    fake_bs4 = types.ModuleType("bs4")

    def _extract_tag(html, tag):
        match = re.search(f"<{tag}[^>]*>(.*?)</{tag}>", html, re.IGNORECASE | re.DOTALL)
        return match.group(1).strip() if match else ""

    class _FakeTag:
        def __init__(self, text):
            self.string = text

    class _FakeSoup:
        def __init__(self, html, parser=None):
            self._html = html
            title = _extract_tag(html, "title")
            self.title = _FakeTag(title) if title else None

        def __call__(self, names):
            return []

        def find_all(self, name, **kwargs):
            return []

        def get_text(self, separator="\n", strip=False):
            text = re.sub(r"<[^>]+>", " ", self._html)
            if strip:
                text = text.strip()
            words = text.split()
            if separator != " ":
                return separator.join(words)
            return " ".join(words)

    fake_bs4.BeautifulSoup = _FakeSoup
    sys.modules["bs4"] = fake_bs4

file_tools = importlib.import_module("omniagents.builtin.tools.file_tools")


def unwrap(tool):
    impl = tool.on_invoke_tool.__closure__[0].cell_contents
    async_wrapper = impl.__closure__[-1].cell_contents
    closure = getattr(async_wrapper, "__closure__", None)
    if closure:
        return closure[0].cell_contents
    return async_wrapper


def make_ctx(root):
    return types.SimpleNamespace(context={"workspace_root": str(root)})


def test_glob_files_matches(tmp_path):
    ctx = make_ctx(tmp_path)
    first = tmp_path / "folder"
    first.mkdir()
    file_a = first / "a.txt"
    file_a.write_text("content", encoding="utf-8")
    file_b = tmp_path / "b.txt"
    file_b.write_text("more", encoding="utf-8")

    result = unwrap(file_tools.glob_files)(ctx, "**/*.txt")

    paths = result.ui_metadata["value"]
    assert str(file_a) in paths
    assert str(file_b) in paths
    assert result.ui_metadata["metadata"]["result_count"] == len(paths)


def test_glob_files_respects_hidden(tmp_path):
    ctx = make_ctx(tmp_path)
    visible = tmp_path / "visible.txt"
    visible.write_text("x", encoding="utf-8")
    hidden = tmp_path / ".hidden.txt"
    hidden.write_text("y", encoding="utf-8")

    result = unwrap(file_tools.glob_files)(ctx, "*.txt", ignore_hidden=True)

    paths = result.ui_metadata["value"]
    assert str(visible) in paths
    assert str(hidden) not in paths


def test_glob_files_single_file_path(tmp_path):
    ctx = make_ctx(tmp_path)
    target = tmp_path / "only.txt"
    target.write_text("data", encoding="utf-8")

    result = unwrap(file_tools.glob_files)(ctx, "only.txt", path=str(target))

    paths = result.ui_metadata["value"]
    assert paths == [str(target)]


def test_grep_files_find_matches(tmp_path):
    ctx = make_ctx(tmp_path)
    file_a = tmp_path / "find.txt"
    file_a.write_text("alpha\nbeta\nalpha", encoding="utf-8")
    other = tmp_path / "skip.md"
    other.write_text("alpha", encoding="utf-8")

    result = unwrap(file_tools.grep_files)(ctx, "alpha", include="*.txt")

    matches = result.ui_metadata["value"]
    assert len(matches) == 1
    entry = matches[0]
    assert entry["file"] == str(file_a)
    assert entry["match_count"] == 2


def test_grep_files_invalid_pattern(tmp_path):
    ctx = make_ctx(tmp_path)

    result = unwrap(file_tools.grep_files)(ctx, "(")

    assert "Invalid regex pattern" in result.llm_output
    assert result.ui_metadata["metadata"]["error_type"] == "grep_error"


def test_grep_files_respects_limit(tmp_path):
    """Test that limit parameter caps the number of files returned."""
    ctx = make_ctx(tmp_path)

    # Create 10 files with matches
    for i in range(10):
        (tmp_path / f"file_{i:02d}.txt").write_text(f"alpha {i}", encoding="utf-8")

    result = unwrap(file_tools.grep_files)(ctx, "alpha", limit=3)

    assert result.ui_metadata["metadata"]["files_returned"] == 3
    assert result.ui_metadata["metadata"]["limit"] == 3
    assert "showing 3" in result.llm_output.lower()


def test_grep_files_returns_file_paths_and_counts_only(tmp_path):
    """Test that grep_files returns only file paths and match counts, not content."""
    ctx = make_ctx(tmp_path)

    # Create file with matches
    content = "\n".join([f"match line {i}" for i in range(20)])
    (tmp_path / "many_matches.txt").write_text(content, encoding="utf-8")

    result = unwrap(file_tools.grep_files)(ctx, "match")

    # Should have file path and count, but no content
    file_result = result.ui_metadata["value"][0]
    assert "file" in file_result
    assert "match_count" in file_result
    assert file_result["match_count"] == 20
    # No content/matches key
    assert "matches" not in file_result
    assert "content" not in file_result
    # LLM output should not contain the actual line content
    assert "match line" not in result.llm_output


def test_grep_files_reports_elapsed_time(tmp_path):
    """Test that elapsed time is reported in metadata."""
    ctx = make_ctx(tmp_path)
    (tmp_path / "test.txt").write_text("alpha", encoding="utf-8")

    result = unwrap(file_tools.grep_files)(ctx, "alpha")

    assert "elapsed_ms" in result.ui_metadata["metadata"]
    assert result.ui_metadata["metadata"]["elapsed_ms"] >= 0


def test_grep_files_shows_truncation_notice(tmp_path):
    """Test that truncation notices are shown when hitting limits."""
    ctx = make_ctx(tmp_path)

    # Create many files
    for i in range(20):
        (tmp_path / f"file_{i:02d}.txt").write_text(f"pattern {i}", encoding="utf-8")

    result = unwrap(file_tools.grep_files)(ctx, "pattern", limit=5)

    assert "showing 5" in result.llm_output.lower()
    assert "limit=" in result.llm_output
    assert result.ui_metadata["truncated"] is True


def test_grep_files_no_matches_shows_skip_info(tmp_path):
    """Test that skip information is shown when no matches found."""
    ctx = make_ctx(tmp_path)
    (tmp_path / "test.txt").write_text("no match here", encoding="utf-8")
    (tmp_path / ".hidden.txt").write_text("also no match", encoding="utf-8")

    result = unwrap(file_tools.grep_files)(
        ctx, "nonexistent", ignore_hidden=True
    )

    assert "No matches found" in result.llm_output
    assert result.ui_metadata["metadata"]["skipped_hidden"] >= 0


def test_grep_files_counts_all_matches_in_file(tmp_path):
    """Test that all matches in a file are counted."""
    ctx = make_ctx(tmp_path)
    content = "alpha\nbeta\nalpha again\nalpha third"
    (tmp_path / "test.txt").write_text(content, encoding="utf-8")

    result = unwrap(file_tools.grep_files)(ctx, "alpha")

    file_result = result.ui_metadata["value"][0]
    assert file_result["match_count"] == 3
    # Output shows the count
    assert "3 matches" in result.llm_output


# --- read_file tests ---


def test_read_file_basic(tmp_path):
    """Test basic file reading with line numbers."""
    ctx = make_ctx(tmp_path)
    file = tmp_path / "test.txt"
    file.write_text("line one\nline two\nline three\n", encoding="utf-8")

    result = unwrap(file_tools.read_file)(ctx, str(file))

    assert "L1: line one" in result.llm_output
    assert "L2: line two" in result.llm_output
    assert "L3: line three" in result.llm_output
    assert result.ui_metadata["metadata"]["lines_returned"] == 3


def test_read_file_with_offset_and_limit(tmp_path):
    """Test reading with start_line and num_lines."""
    ctx = make_ctx(tmp_path)
    file = tmp_path / "test.txt"
    lines = [f"line {i}\n" for i in range(1, 11)]
    file.write_text("".join(lines), encoding="utf-8")

    result = unwrap(file_tools.read_file)(ctx, str(file), start_line=3, num_lines=4)

    assert "L3: line 3" in result.llm_output
    assert "L6: line 6" in result.llm_output
    assert "L2:" not in result.llm_output
    assert "L7:" not in result.llm_output
    assert result.ui_metadata["metadata"]["lines_returned"] == 4


def test_read_file_truncates_long_lines(tmp_path):
    """Test that lines longer than 500 chars are truncated."""
    ctx = make_ctx(tmp_path)
    file = tmp_path / "test.txt"
    long_line = "x" * 600
    file.write_text(f"short\n{long_line}\nafter\n", encoding="utf-8")

    result = unwrap(file_tools.read_file)(ctx, str(file))

    assert result.ui_metadata["metadata"]["lines_truncated"] == 1
    assert "exceeded 500 chars" in result.llm_output
    # The long line should be truncated
    lines = result.llm_output.split("\n")
    long_output_line = [l for l in lines if l.startswith("L2:")][0]
    # Line content (after "L2: ") should be <= 500 chars
    assert len(long_output_line) <= 504 + 10  # "L2: " + 500 chars + some margin


def test_read_file_shows_truncation_notice(tmp_path):
    """Test that truncation notice appears when hitting the limit."""
    ctx = make_ctx(tmp_path)
    file = tmp_path / "test.txt"
    lines = [f"line {i}\n" for i in range(1, 101)]
    file.write_text("".join(lines), encoding="utf-8")

    result = unwrap(file_tools.read_file)(ctx, str(file), num_lines=10)

    assert "90 more lines not shown" in result.llm_output
    assert "Use start_line=11 to continue" in result.llm_output
    assert result.ui_metadata["metadata"]["was_content_truncated"] is True


def test_read_file_default_limit(tmp_path):
    """Test that default limit of 2000 lines is applied."""
    ctx = make_ctx(tmp_path)
    file = tmp_path / "test.txt"
    lines = [f"line {i}\n" for i in range(1, 2100)]
    file.write_text("".join(lines), encoding="utf-8")

    result = unwrap(file_tools.read_file)(ctx, str(file))

    assert result.ui_metadata["metadata"]["lines_returned"] == 2000
    assert result.ui_metadata["metadata"]["total_file_lines"] == 2099
    assert "more lines not shown" in result.llm_output


def test_read_file_indentation_mode_basic(tmp_path):
    """Test indentation mode captures a code block."""
    ctx = make_ctx(tmp_path)
    file = tmp_path / "test.py"
    content = """def outer():
    if condition:
        inner()
    tail()
"""
    file.write_text(content, encoding="utf-8")

    result = unwrap(file_tools.read_file)(
        ctx, str(file), mode="indentation", anchor_line=3, max_levels=1
    )

    # Should capture the if block (lines 2-3) based on indentation
    assert "L2:" in result.llm_output
    assert "L3:" in result.llm_output
    assert result.ui_metadata["metadata"]["mode"] == "indentation"


def test_read_file_indentation_mode_expands_parents(tmp_path):
    """Test indentation mode expands to include parent blocks."""
    ctx = make_ctx(tmp_path)
    file = tmp_path / "test.py"
    content = """class Foo:
    def method(self):
        if cond:
            do_thing()
        return
"""
    file.write_text(content, encoding="utf-8")

    result = unwrap(file_tools.read_file)(
        ctx, str(file), mode="indentation", anchor_line=4, max_levels=2
    )

    # Should capture up to method level (2 levels up from do_thing)
    assert "L2:" in result.llm_output  # def method
    assert "L3:" in result.llm_output  # if cond
    assert "L4:" in result.llm_output  # do_thing
    assert "L5:" in result.llm_output  # return


def test_read_file_error_on_invalid_start_line(tmp_path):
    """Test error when start_line exceeds file length."""
    ctx = make_ctx(tmp_path)
    file = tmp_path / "test.txt"
    file.write_text("one\ntwo\n", encoding="utf-8")

    result = unwrap(file_tools.read_file)(ctx, str(file), start_line=100)

    assert "exceeds file length" in result.llm_output
    assert result.ui_metadata["display_type"] == "error"


def test_read_file_error_on_invalid_anchor(tmp_path):
    """Test error when anchor_line is invalid in indentation mode."""
    ctx = make_ctx(tmp_path)
    file = tmp_path / "test.txt"
    file.write_text("one\ntwo\n", encoding="utf-8")

    result = unwrap(file_tools.read_file)(
        ctx, str(file), mode="indentation", anchor_line=100
    )

    assert "out of range" in result.llm_output
    assert result.ui_metadata["display_type"] == "error"


def test_read_file_preserves_header_comments(tmp_path):
    """Test that header comments are included in indentation mode."""
    ctx = make_ctx(tmp_path)
    file = tmp_path / "test.py"
    content = """# This is a docstring
def my_function():
    return 42
"""
    file.write_text(content, encoding="utf-8")

    result = unwrap(file_tools.read_file)(
        ctx, str(file), mode="indentation", anchor_line=3, max_levels=1, include_header=True
    )

    # Should include the comment header
    assert "L1:" in result.llm_output or "L2:" in result.llm_output


# --- list_directory tests ---


def test_list_directory_basic(tmp_path):
    """Test basic directory listing with indented tree output."""
    ctx = make_ctx(tmp_path)
    (tmp_path / "file.txt").write_text("content", encoding="utf-8")
    (tmp_path / "subdir").mkdir()
    (tmp_path / "subdir" / "nested.txt").write_text("nested", encoding="utf-8")

    result = unwrap(file_tools.list_directory)(ctx, str(tmp_path))

    assert f"Absolute path: {tmp_path}" in result.llm_output
    assert "file.txt" in result.llm_output
    assert "subdir/" in result.llm_output
    assert "nested.txt" in result.llm_output
    assert result.ui_metadata["metadata"]["total_entries"] == 3


def test_list_directory_with_pagination(tmp_path):
    """Test pagination with offset and limit."""
    ctx = make_ctx(tmp_path)
    for i in range(10):
        (tmp_path / f"file_{i:02d}.txt").write_text(f"content {i}", encoding="utf-8")

    # Get first 5
    result1 = unwrap(file_tools.list_directory)(ctx, str(tmp_path), limit=5)
    assert result1.ui_metadata["metadata"]["entries_returned"] == 5
    assert "5 more entries" in result1.llm_output
    assert "Use offset=6 to continue" in result1.llm_output

    # Get next 5
    result2 = unwrap(file_tools.list_directory)(ctx, str(tmp_path), offset=6, limit=5)
    assert result2.ui_metadata["metadata"]["entries_returned"] == 5
    assert result2.ui_metadata["metadata"]["was_truncated"] is False


def test_list_directory_depth_control(tmp_path):
    """Test depth parameter limits recursion."""
    ctx = make_ctx(tmp_path)
    level1 = tmp_path / "level1"
    level2 = level1 / "level2"
    level3 = level2 / "level3"
    level3.mkdir(parents=True)
    (level3 / "deep.txt").write_text("deep", encoding="utf-8")

    # Depth 1: only top level
    result1 = unwrap(file_tools.list_directory)(ctx, str(tmp_path), depth=1)
    assert "level1/" in result1.llm_output
    assert "level2" not in result1.llm_output

    # Depth 2: two levels
    result2 = unwrap(file_tools.list_directory)(ctx, str(tmp_path), depth=2)
    assert "level1/" in result2.llm_output
    assert "level2/" in result2.llm_output
    assert "level3" not in result2.llm_output

    # Depth 4: all levels
    result4 = unwrap(file_tools.list_directory)(ctx, str(tmp_path), depth=4)
    assert "deep.txt" in result4.llm_output


def test_list_directory_indentation(tmp_path):
    """Test that nested entries are properly indented."""
    ctx = make_ctx(tmp_path)
    subdir = tmp_path / "parent"
    subdir.mkdir()
    (subdir / "child.txt").write_text("child", encoding="utf-8")

    result = unwrap(file_tools.list_directory)(ctx, str(tmp_path), depth=2)

    lines = result.llm_output.split("\n")
    # Find the child.txt line - it should be indented
    child_line = [l for l in lines if "child.txt" in l][0]
    assert child_line.startswith("  ")  # 2 spaces for depth 1


def test_list_directory_symlink_detection(tmp_path):
    """Test that symlinks are marked with @ suffix."""
    ctx = make_ctx(tmp_path)
    target = tmp_path / "target.txt"
    target.write_text("target", encoding="utf-8")
    link = tmp_path / "link"
    try:
        link.symlink_to(target)
    except OSError:
        # Skip on systems that don't support symlinks
        return

    result = unwrap(file_tools.list_directory)(ctx, str(tmp_path))

    assert "link@" in result.llm_output
    assert result.ui_metadata["metadata"]["symlink_count"] == 1


def test_list_directory_truncates_long_names(tmp_path):
    """Test that long entry names are truncated by _truncate_entry_name."""
    # We can't create a 600-char filename on most filesystems,
    # so test the truncation function directly
    long_name = "x" * 600 + ".txt"
    truncated = file_tools._truncate_entry_name(long_name)
    assert len(truncated) <= 500

    # Also verify with a real file that has a moderately long name
    ctx = make_ctx(tmp_path)
    moderate_name = "a" * 200 + ".txt"
    (tmp_path / moderate_name).write_text("content", encoding="utf-8")

    result = unwrap(file_tools.list_directory)(ctx, str(tmp_path))
    entries = result.ui_metadata["value"]["entries"]
    assert len(entries) == 1
    # Name should be preserved since it's under 500
    assert entries[0]["name"] == moderate_name


def test_list_directory_error_on_invalid_offset(tmp_path):
    """Test error when offset exceeds entry count."""
    ctx = make_ctx(tmp_path)
    (tmp_path / "file.txt").write_text("content", encoding="utf-8")

    result = unwrap(file_tools.list_directory)(ctx, str(tmp_path), offset=100)

    assert "exceeds entry count" in result.llm_output
    assert result.ui_metadata["display_type"] == "error"


def test_list_directory_empty(tmp_path):
    """Test empty directory handling."""
    ctx = make_ctx(tmp_path)
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()

    result = unwrap(file_tools.list_directory)(ctx, str(empty_dir))

    assert "empty directory" in result.llm_output.lower()
    assert result.ui_metadata["metadata"]["total_entries"] == 0


def test_list_directory_respects_hidden(tmp_path):
    """Test ignore_hidden parameter."""
    ctx = make_ctx(tmp_path)
    (tmp_path / "visible.txt").write_text("visible", encoding="utf-8")
    (tmp_path / ".hidden.txt").write_text("hidden", encoding="utf-8")

    # With ignore_hidden=True
    result = unwrap(file_tools.list_directory)(ctx, str(tmp_path), ignore_hidden=True)
    assert "visible.txt" in result.llm_output
    assert ".hidden.txt" not in result.llm_output

    # With ignore_hidden=False (default)
    result2 = unwrap(file_tools.list_directory)(ctx, str(tmp_path), ignore_hidden=False)
    assert ".hidden.txt" in result2.llm_output


def test_list_directory_not_a_directory(tmp_path):
    """Test error when path is not a directory."""
    ctx = make_ctx(tmp_path)
    file = tmp_path / "file.txt"
    file.write_text("content", encoding="utf-8")

    result = unwrap(file_tools.list_directory)(ctx, str(file))

    assert "not a directory" in result.llm_output.lower()
    assert result.ui_metadata["display_type"] == "error"
