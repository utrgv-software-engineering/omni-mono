import re
import subprocess
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

import omniagents.builtin.tools.bash_tool as bash_tool


def unwrap(tool):
    impl = tool.on_invoke_tool.__closure__[0].cell_contents
    async_wrapper = impl.__closure__[-1].cell_contents
    closure = getattr(async_wrapper, "__closure__", None)
    if closure:
        return closure[0].cell_contents
    return async_wrapper


def make_ctx(tmp_path):
    return types.SimpleNamespace(context={"workspace_root": str(tmp_path)})


def test_execute_bash_success(monkeypatch, tmp_path):
    ctx = make_ctx(tmp_path)

    def fake_run(command, shell, capture_output, text, timeout, cwd):
        assert command == "echo ok"
        assert shell is True
        assert capture_output is True
        assert text is True
        assert timeout == 30
        assert cwd == str(tmp_path)
        return types.SimpleNamespace(stdout="ok\n", stderr="", returncode=0)

    monkeypatch.setattr(bash_tool.subprocess, "run", fake_run)

    result = unwrap(bash_tool.execute_bash)(ctx, "echo ok")

    assert "Exit code: 0 (success)" in result.llm_output
    assert "ok" in result.llm_output
    assert result.ui_metadata["metadata"]["stdout"] == "ok\n"
    assert result.ui_metadata["metadata"]["success"] is True
    assert "wall_time_ms" in result.ui_metadata["metadata"]


def test_execute_bash_failure(monkeypatch, tmp_path):
    ctx = make_ctx(tmp_path)

    def fake_run(command, shell, capture_output, text, timeout, cwd):
        return types.SimpleNamespace(stdout="out", stderr="err", returncode=2)

    monkeypatch.setattr(bash_tool.subprocess, "run", fake_run)

    result = unwrap(bash_tool.execute_bash)(ctx, "bad command")

    assert "Exit code: 2 (failed)" in result.llm_output
    assert result.ui_metadata["metadata"]["success"] is False
    assert result.ui_metadata["metadata"]["has_stderr"] is True


def test_execute_bash_timeout(monkeypatch, tmp_path):
    ctx = make_ctx(tmp_path)

    def fake_run(command, shell, capture_output, text, timeout, cwd):
        raise subprocess.TimeoutExpired(command, timeout)

    monkeypatch.setattr(bash_tool.subprocess, "run", fake_run)

    result = unwrap(bash_tool.execute_bash)(ctx, "sleep 5", timeout=1)

    assert "Command timed out" in result.llm_output
    assert result.ui_metadata["metadata"]["error_type"] == "timeout"
    assert "wall_time_ms" in result.ui_metadata["metadata"]


def test_execute_bash_unexpected_error(monkeypatch, tmp_path):
    ctx = make_ctx(tmp_path)

    def fake_run(command, shell, capture_output, text, timeout, cwd):
        raise RuntimeError("boom")

    monkeypatch.setattr(bash_tool.subprocess, "run", fake_run)

    result = unwrap(bash_tool.execute_bash)(ctx, "explode")

    assert result.llm_output == "Error executing command: boom"
    assert result.ui_metadata["metadata"]["error_type"] == "execution_error"


# --- New tests for truncation and output handling ---


def test_execute_bash_truncates_large_output(monkeypatch, tmp_path):
    """Test that large output is truncated with head/tail preservation."""
    ctx = make_ctx(tmp_path)

    # Generate multi-line output larger than max_output_chars
    # Use multiple lines so line truncation doesn't interfere
    large_output = "\n".join(["A" * 50] * 20 + ["B" * 50] * 20 + ["C" * 50] * 20)

    def fake_run(command, shell, capture_output, text, timeout, cwd):
        return types.SimpleNamespace(stdout=large_output, stderr="", returncode=0)

    monkeypatch.setattr(bash_tool.subprocess, "run", fake_run)

    # Use small max_output_chars to force truncation
    result = unwrap(bash_tool.execute_bash)(ctx, "big output", max_output_chars=500)

    assert "characters truncated" in result.llm_output
    # Should preserve head (A's) and tail (C's)
    assert "AAA" in result.llm_output
    assert "CCC" in result.llm_output
    assert result.ui_metadata["metadata"]["was_truncated"] is True
    assert result.ui_metadata["metadata"]["chars_truncated"] > 0


def test_execute_bash_truncates_long_lines(monkeypatch, tmp_path):
    """Test that individual long lines are truncated."""
    ctx = make_ctx(tmp_path)

    long_line = "x" * 600  # Exceeds 500 char limit

    def fake_run(command, shell, capture_output, text, timeout, cwd):
        return types.SimpleNamespace(stdout=long_line, stderr="", returncode=0)

    monkeypatch.setattr(bash_tool.subprocess, "run", fake_run)

    result = unwrap(bash_tool.execute_bash)(ctx, "long line")

    assert "lines exceeded 500 chars" in result.llm_output
    assert result.ui_metadata["metadata"]["lines_truncated"] == 1
    # The line should be truncated with "..."
    assert "..." in result.llm_output


def test_execute_bash_reports_wall_time(monkeypatch, tmp_path):
    """Test that wall time is reported in output."""
    ctx = make_ctx(tmp_path)

    def fake_run(command, shell, capture_output, text, timeout, cwd):
        return types.SimpleNamespace(stdout="done", stderr="", returncode=0)

    monkeypatch.setattr(bash_tool.subprocess, "run", fake_run)

    result = unwrap(bash_tool.execute_bash)(ctx, "fast command")

    assert "Wall time:" in result.llm_output
    assert "ms" in result.llm_output
    assert result.ui_metadata["metadata"]["wall_time_ms"] >= 0


def test_execute_bash_no_output(monkeypatch, tmp_path):
    """Test handling of commands with no output."""
    ctx = make_ctx(tmp_path)

    def fake_run(command, shell, capture_output, text, timeout, cwd):
        return types.SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.setattr(bash_tool.subprocess, "run", fake_run)

    result = unwrap(bash_tool.execute_bash)(ctx, "silent")

    assert "(no output)" in result.llm_output
    assert "Exit code: 0 (success)" in result.llm_output


def test_execute_bash_truncation_notice_with_stats(monkeypatch, tmp_path):
    """Test that truncation notices include helpful stats."""
    ctx = make_ctx(tmp_path)

    # Generate multi-line output
    output = "\n".join([f"line {i}" for i in range(100)])

    def fake_run(command, shell, capture_output, text, timeout, cwd):
        return types.SimpleNamespace(stdout=output, stderr="", returncode=0)

    monkeypatch.setattr(bash_tool.subprocess, "run", fake_run)

    result = unwrap(bash_tool.execute_bash)(ctx, "many lines", max_output_chars=200)

    assert "Total output:" in result.llm_output
    assert "chars" in result.llm_output
    assert "lines" in result.llm_output
    assert result.ui_metadata["metadata"]["total_lines"] == 100


def test_truncate_output_head_tail_preserves_boundaries():
    """Test the head/tail truncation helper function directly."""
    text = "AAAA" + "BBBB" * 100 + "CCCC"

    truncated, chars_removed, was_truncated = bash_tool._truncate_output_head_tail(text, 20)

    assert was_truncated is True
    assert chars_removed > 0
    assert truncated.startswith("AA")  # Head preserved
    assert truncated.endswith("CC")   # Tail preserved
    assert "characters truncated" in truncated


def test_truncate_output_head_tail_no_truncation_needed():
    """Test that small output is not truncated."""
    text = "short output"

    truncated, chars_removed, was_truncated = bash_tool._truncate_output_head_tail(text, 1000)

    assert was_truncated is False
    assert chars_removed == 0
    assert truncated == text
