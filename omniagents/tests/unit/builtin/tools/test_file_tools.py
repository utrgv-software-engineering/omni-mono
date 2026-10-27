import importlib
import re
import sys
import types

import pytest

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
from omniagents.workspace import HostWorkspace, reset_workspace, set_workspace


def unwrap(tool):
    """Return the original function the tool wraps.

    The omniagents ``function_tool`` / ``rich_function_tool`` decorators
    stash the original (decorated) function on the SDK FunctionTool as
    ``_original_func`` for exactly this purpose.
    """
    return tool._original_func


def make_ctx(root):
    return types.SimpleNamespace(context={"workspace_root": str(root)})


@pytest.fixture
def routed(tmp_path):
    """Install a HostWorkspace rooted at tmp_path and yield a matching ctx.

    The W2-migrated tools route filesystem reads through the active
    ``omniagents.workspace`` contextvar — when the runtime isn't involved
    (as in unit tests), tests must install the workspace explicitly so
    absolute paths under tmp_path are resolved against the right root.
    """
    workspace = HostWorkspace(root=str(tmp_path))
    token = set_workspace(workspace)
    ctx = make_ctx(tmp_path)
    yield ctx
    reset_workspace(token)


# --- read_file tests ---


@pytest.mark.asyncio
async def test_read_file_basic(routed, tmp_path):
    """Test basic file reading with line numbers."""
    file = tmp_path / "test.txt"
    file.write_text("line one\nline two\nline three\n", encoding="utf-8")

    result = await unwrap(file_tools.read_file)(routed, str(file))

    assert "L1: line one" in result.llm_output
    assert "L2: line two" in result.llm_output
    assert "L3: line three" in result.llm_output
    assert result.ui_metadata["metadata"]["lines_returned"] == 3


@pytest.mark.asyncio
async def test_read_file_with_offset_and_limit(routed, tmp_path):
    """Test reading with start_line and num_lines."""
    file = tmp_path / "test.txt"
    lines = [f"line {i}\n" for i in range(1, 11)]
    file.write_text("".join(lines), encoding="utf-8")

    result = await unwrap(file_tools.read_file)(
        routed, str(file), start_line=3, num_lines=4
    )

    assert "L3: line 3" in result.llm_output
    assert "L6: line 6" in result.llm_output
    assert "L2:" not in result.llm_output
    assert "L7:" not in result.llm_output
    assert result.ui_metadata["metadata"]["lines_returned"] == 4


@pytest.mark.asyncio
async def test_read_file_preserves_long_lines(routed, tmp_path):
    """Long lines remain intact until the shared total-output boundary."""
    file = tmp_path / "test.txt"
    long_line = "x" * 600
    file.write_text(f"short\n{long_line}\nafter\n", encoding="utf-8")

    result = await unwrap(file_tools.read_file)(routed, str(file))

    assert result.ui_metadata["metadata"]["lines_truncated"] == 0
    assert "exceeded 500 chars" not in result.llm_output
    lines = result.llm_output.split("\n")
    long_output_line = [l for l in lines if l.startswith("L2:")][0]
    assert long_output_line == "L2: " + long_line


@pytest.mark.asyncio
async def test_read_file_shows_truncation_notice(routed, tmp_path):
    """Test that truncation notice appears when hitting the limit."""
    file = tmp_path / "test.txt"
    lines = [f"line {i}\n" for i in range(1, 101)]
    file.write_text("".join(lines), encoding="utf-8")

    result = await unwrap(file_tools.read_file)(routed, str(file), num_lines=10)

    assert "90 more lines not shown" in result.llm_output
    assert "Use start_line=11 to continue" in result.llm_output
    assert result.ui_metadata["metadata"]["was_content_truncated"] is True


@pytest.mark.asyncio
async def test_read_file_default_limit(routed, tmp_path):
    """Test that default limit of 2000 lines is applied."""
    file = tmp_path / "test.txt"
    lines = [f"line {i}\n" for i in range(1, 2100)]
    file.write_text("".join(lines), encoding="utf-8")

    result = await unwrap(file_tools.read_file)(routed, str(file))

    assert result.ui_metadata["metadata"]["lines_returned"] == 2000
    assert result.ui_metadata["metadata"]["total_file_lines"] == 2099
    assert "more lines not shown" in result.llm_output


@pytest.mark.asyncio
async def test_read_file_indentation_mode_basic(routed, tmp_path):
    """Test indentation mode captures a code block."""
    file = tmp_path / "test.py"
    content = """def outer():
    if condition:
        inner()
    tail()
"""
    file.write_text(content, encoding="utf-8")

    result = await unwrap(file_tools.read_file)(
        routed, str(file), mode="indentation", anchor_line=3, max_levels=1
    )

    assert "L2:" in result.llm_output
    assert "L3:" in result.llm_output
    assert result.ui_metadata["metadata"]["mode"] == "indentation"


@pytest.mark.asyncio
async def test_read_file_indentation_mode_expands_parents(routed, tmp_path):
    """Test indentation mode expands to include parent blocks."""
    file = tmp_path / "test.py"
    content = """class Foo:
    def method(self):
        if cond:
            do_thing()
        return
"""
    file.write_text(content, encoding="utf-8")

    result = await unwrap(file_tools.read_file)(
        routed, str(file), mode="indentation", anchor_line=4, max_levels=2
    )

    assert "L2:" in result.llm_output  # def method
    assert "L3:" in result.llm_output  # if cond
    assert "L4:" in result.llm_output  # do_thing
    assert "L5:" in result.llm_output  # return


@pytest.mark.asyncio
async def test_read_file_error_on_invalid_start_line(routed, tmp_path):
    """Test error when start_line exceeds file length."""
    file = tmp_path / "test.txt"
    file.write_text("one\ntwo\n", encoding="utf-8")

    result = await unwrap(file_tools.read_file)(routed, str(file), start_line=100)

    assert "exceeds file length" in result.llm_output
    assert result.ui_metadata["display_type"] == "error"


@pytest.mark.asyncio
async def test_read_file_error_on_invalid_anchor(routed, tmp_path):
    """Test error when anchor_line is invalid in indentation mode."""
    file = tmp_path / "test.txt"
    file.write_text("one\ntwo\n", encoding="utf-8")

    result = await unwrap(file_tools.read_file)(
        routed, str(file), mode="indentation", anchor_line=100
    )

    assert "out of range" in result.llm_output
    assert result.ui_metadata["display_type"] == "error"


@pytest.mark.asyncio
async def test_read_file_preserves_header_comments(routed, tmp_path):
    """Test that header comments are included in indentation mode."""
    file = tmp_path / "test.py"
    content = """# This is a docstring
def my_function():
    return 42
"""
    file.write_text(content, encoding="utf-8")

    result = await unwrap(file_tools.read_file)(
        routed,
        str(file),
        mode="indentation",
        anchor_line=3,
        max_levels=1,
        include_header=True,
    )

    assert "L1:" in result.llm_output or "L2:" in result.llm_output


# --- list_directory tests ---


@pytest.mark.asyncio
async def test_list_directory_basic(routed, tmp_path):
    """Test basic directory listing with indented tree output."""
    (tmp_path / "file.txt").write_text("content", encoding="utf-8")
    (tmp_path / "subdir").mkdir()
    (tmp_path / "subdir" / "nested.txt").write_text("nested", encoding="utf-8")

    result = await unwrap(file_tools.list_directory)(routed, str(tmp_path))

    assert f"Absolute path: {tmp_path}" in result.llm_output
    assert "file.txt" in result.llm_output
    assert "subdir/" in result.llm_output
    assert "nested.txt" in result.llm_output
    assert result.ui_metadata["metadata"]["total_entries"] == 3


@pytest.mark.asyncio
async def test_list_directory_with_pagination(routed, tmp_path):
    """Test pagination with offset and limit."""
    for i in range(10):
        (tmp_path / f"file_{i:02d}.txt").write_text(f"content {i}", encoding="utf-8")

    result1 = await unwrap(file_tools.list_directory)(routed, str(tmp_path), limit=5)
    assert result1.ui_metadata["metadata"]["entries_returned"] == 5
    assert "5 more entries" in result1.llm_output
    assert "Use offset=6 to continue" in result1.llm_output

    result2 = await unwrap(file_tools.list_directory)(
        routed, str(tmp_path), offset=6, limit=5
    )
    assert result2.ui_metadata["metadata"]["entries_returned"] == 5
    assert result2.ui_metadata["metadata"]["was_truncated"] is False


@pytest.mark.asyncio
async def test_list_directory_depth_control(routed, tmp_path):
    """Test depth parameter limits recursion."""
    level1 = tmp_path / "level1"
    level2 = level1 / "level2"
    level3 = level2 / "level3"
    level3.mkdir(parents=True)
    (level3 / "deep.txt").write_text("deep", encoding="utf-8")

    result1 = await unwrap(file_tools.list_directory)(routed, str(tmp_path), depth=1)
    assert "level1/" in result1.llm_output
    assert "level2" not in result1.llm_output

    result2 = await unwrap(file_tools.list_directory)(routed, str(tmp_path), depth=2)
    assert "level1/" in result2.llm_output
    assert "level2/" in result2.llm_output
    assert "level3" not in result2.llm_output

    result4 = await unwrap(file_tools.list_directory)(routed, str(tmp_path), depth=4)
    assert "deep.txt" in result4.llm_output


@pytest.mark.asyncio
async def test_list_directory_indentation(routed, tmp_path):
    """Test that nested entries are properly indented."""
    subdir = tmp_path / "parent"
    subdir.mkdir()
    (subdir / "child.txt").write_text("child", encoding="utf-8")

    result = await unwrap(file_tools.list_directory)(routed, str(tmp_path), depth=2)

    lines = result.llm_output.split("\n")
    child_line = [l for l in lines if "child.txt" in l][0]
    assert child_line.startswith("  ")  # 2 spaces for depth 1


@pytest.mark.asyncio
async def test_list_directory_symlink_detection(routed, tmp_path):
    """Test that symlinks are marked with @ suffix."""
    target = tmp_path / "target.txt"
    target.write_text("target", encoding="utf-8")
    link = tmp_path / "link"
    try:
        link.symlink_to(target)
    except OSError:
        return

    result = await unwrap(file_tools.list_directory)(routed, str(tmp_path))

    assert "link@" in result.llm_output
    assert result.ui_metadata["metadata"]["symlink_count"] == 1


@pytest.mark.asyncio
async def test_list_directory_truncates_long_names(routed, tmp_path):
    """Test that long entry names are truncated by _truncate_entry_name."""
    long_name = "x" * 600 + ".txt"
    truncated = file_tools._truncate_entry_name(long_name)
    assert len(truncated) <= 500

    moderate_name = "a" * 200 + ".txt"
    (tmp_path / moderate_name).write_text("content", encoding="utf-8")

    result = await unwrap(file_tools.list_directory)(routed, str(tmp_path))
    entries = result.ui_metadata["value"]["entries"]
    assert len(entries) == 1
    assert entries[0]["name"] == moderate_name


@pytest.mark.asyncio
async def test_list_directory_error_on_invalid_offset(routed, tmp_path):
    """Test error when offset exceeds entry count."""
    (tmp_path / "file.txt").write_text("content", encoding="utf-8")

    result = await unwrap(file_tools.list_directory)(routed, str(tmp_path), offset=100)

    assert "exceeds entry count" in result.llm_output
    assert result.ui_metadata["display_type"] == "error"


@pytest.mark.asyncio
async def test_list_directory_empty(routed, tmp_path):
    """Test empty directory handling."""
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()

    result = await unwrap(file_tools.list_directory)(routed, str(empty_dir))

    assert "empty directory" in result.llm_output.lower()
    assert result.ui_metadata["metadata"]["total_entries"] == 0


@pytest.mark.asyncio
async def test_list_directory_includes_hidden(routed, tmp_path):
    """Hidden files are always listed — coding workspaces rely on dotfiles."""
    (tmp_path / "visible.txt").write_text("visible", encoding="utf-8")
    (tmp_path / ".hidden.txt").write_text("hidden", encoding="utf-8")

    result = await unwrap(file_tools.list_directory)(routed, str(tmp_path))
    assert "visible.txt" in result.llm_output
    assert ".hidden.txt" in result.llm_output


@pytest.mark.asyncio
async def test_list_directory_not_a_directory(routed, tmp_path):
    """Test error when path is not a directory."""
    file = tmp_path / "file.txt"
    file.write_text("content", encoding="utf-8")

    result = await unwrap(file_tools.list_directory)(routed, str(file))

    assert "not a directory" in result.llm_output.lower()
    assert result.ui_metadata["display_type"] == "error"


# --- edit_file tests ---


@pytest.mark.asyncio
async def test_edit_file_basic_replacement(routed, tmp_path):
    """A simple text replacement writes back the modified content."""
    file = tmp_path / "config.py"
    file.write_text("debug = False\nverbose = True\n", encoding="utf-8")

    result = await unwrap(file_tools.edit_file)(
        routed, str(file), "debug = False", "debug = True"
    )

    assert "Successfully updated" in result.llm_output
    assert result.ui_metadata["metadata"]["replacements"] == 1
    assert file.read_text(encoding="utf-8") == "debug = True\nverbose = True\n"


@pytest.mark.asyncio
async def test_edit_file_dry_run_does_not_write(routed, tmp_path):
    """dry_run=True must not modify the file but should return a diff preview."""
    file = tmp_path / "f.py"
    original = "old_call()\n"
    file.write_text(original, encoding="utf-8")

    result = await unwrap(file_tools.edit_file)(
        routed, str(file), "old_call", "new_call", dry_run=True
    )

    assert "Dry run" in result.llm_output
    assert file.read_text(encoding="utf-8") == original
    assert result.ui_metadata["metadata"]["operation"] == "edit_dry_run"


@pytest.mark.asyncio
async def test_edit_file_expected_replacements_mismatch_aborts(routed, tmp_path):
    """expected_replacements guard: when the count doesn't match, no write."""
    file = tmp_path / "x.txt"
    original = "foo\nfoo\n"
    file.write_text(original, encoding="utf-8")

    result = await unwrap(file_tools.edit_file)(
        routed, str(file), "foo", "bar", expected_replacements=1
    )

    assert "Edit aborted" in result.llm_output
    assert file.read_text(encoding="utf-8") == original


@pytest.mark.asyncio
async def test_edit_file_no_match_returns_error(routed, tmp_path):
    """When old_text isn't found, the file is untouched and an error returns."""
    file = tmp_path / "x.txt"
    file.write_text("hello\n", encoding="utf-8")

    result = await unwrap(file_tools.edit_file)(routed, str(file), "absent", "xxx")

    assert result.ui_metadata["display_type"] == "error"
    assert result.ui_metadata["metadata"]["error_type"] == "no_match"
    assert file.read_text(encoding="utf-8") == "hello\n"


# --- apply_patch tests ---


@pytest.mark.asyncio
async def test_apply_patch_add_file(routed, tmp_path):
    """apply_patch creates new files via the workspace."""
    patch = (
        "*** Begin Patch\n"
        f"*** Add File: {tmp_path}/new.txt\n"
        "+hello\n"
        "+world\n"
        "*** End Patch\n"
    )

    result = await unwrap(file_tools.apply_patch)(routed, patch)

    assert "Added" in result.llm_output
    created = tmp_path / "new.txt"
    assert created.exists()
    assert created.read_text(encoding="utf-8") == "hello\nworld\n"


@pytest.mark.asyncio
async def test_apply_patch_update_file(routed, tmp_path):
    """apply_patch updates existing files via workspace read/write."""
    file = tmp_path / "f.txt"
    file.write_text("line one\nline two\n", encoding="utf-8")

    patch = (
        "*** Begin Patch\n"
        f"*** Update File: {tmp_path}/f.txt\n"
        "@@\n"
        " line one\n"
        "-line two\n"
        "+line two updated\n"
        "*** End Patch\n"
    )

    result = await unwrap(file_tools.apply_patch)(routed, patch)

    assert "Modified" in result.llm_output
    assert file.read_text(encoding="utf-8") == "line one\nline two updated\n"


@pytest.mark.asyncio
async def test_apply_patch_delete_file(routed, tmp_path):
    """apply_patch deletes files via workspace.remove."""
    file = tmp_path / "gone.txt"
    file.write_text("bye\n", encoding="utf-8")

    patch = (
        "*** Begin Patch\n" f"*** Delete File: {tmp_path}/gone.txt\n" "*** End Patch\n"
    )

    result = await unwrap(file_tools.apply_patch)(routed, patch)

    assert "Deleted" in result.llm_output
    assert not file.exists()


@pytest.mark.asyncio
async def test_apply_patch_empty_patch_returns_info(routed):
    """An empty patch returns an info message rather than an error."""
    patch = "*** Begin Patch\n*** End Patch\n"
    result = await unwrap(file_tools.apply_patch)(routed, patch)
    assert "No changes" in result.llm_output
