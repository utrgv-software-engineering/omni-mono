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

    assert result.ui_metadata["summary"] == "Pattern search failed"
    assert result.ui_metadata["metadata"]["error_type"] == "grep_error"
