import os
import re
import difflib
from typing import List, Optional, Dict, Tuple, Any
from omniagents.core.tools import rich_function_tool, RichToolOutput, approval_metadata
from functools import lru_cache

try:
    import pygit2
except ImportError:  # pragma: no cover - runtimes without libgit2
    # pygit2 is a native extension and is unavailable in some runtimes
    # (notably Pyodide/WebAssembly). It is only used for gitignore
    # awareness, which already degrades to "nothing is ignored", so the
    # file tools stay fully usable without it.
    pygit2 = None
from agents.run_context import RunContextWrapper

# ----------------------------
# Hidden/Git ignore utilities
# ----------------------------

# --- libgit2-backed ignore (no fallbacks) ---


@lru_cache(maxsize=16)
def _get_pygit2_repo(start_dir: str = "."):
    """Discover and return a pygit2.Repository for start_dir.

    Returns None if start_dir is not inside a Git repository or if the
    repository is malformed / cannot be opened.
    """
    if pygit2 is None:
        return None
    abspath = os.path.abspath(start_dir)
    try:
        gitdir = pygit2.discover_repository(abspath)
        if not gitdir:
            return None
        return pygit2.Repository(gitdir)
    except Exception:
        return None


@lru_cache(maxsize=65536)
def _is_ignored(path: str, repo_root: str = ".") -> bool:
    """Return True if path is ignored by Git, using libgit2 via pygit2 only.

    No subprocess and no pathspec fallback. Outside a Git repo, returns False.
    """
    repo = _get_pygit2_repo(repo_root)
    if repo is None or not getattr(repo, "workdir", None):
        # Not in a Git repo; Git ignore semantics don't apply
        return False

    abspath = os.path.abspath(path)
    try:
        rel = os.path.relpath(abspath, repo.workdir)
    except ValueError:
        # Different drive or otherwise outside workdir
        return False

    # Outside workdir => not ignored by this repo
    if rel.startswith(".."):
        return False

    # Prefer native ignore API which handles both files and directories
    if hasattr(repo, "path_is_ignored"):
        # For directories, we need to add a trailing slash for proper gitignore matching
        check_path = rel
        if os.path.isdir(abspath) and not check_path.endswith("/"):
            check_path = rel + "/"
        return bool(repo.path_is_ignored(check_path))

    # status_file only works for files, not directories
    if os.path.isdir(abspath):
        # Without is_path_ignored, we need to check .gitignore patterns manually
        # For now, return False to avoid errors (directories won't be filtered)
        return False

    try:
        flags = repo.status_file(rel)
        ignored_flag = getattr(pygit2, "GIT_STATUS_IGNORED", 0x1000)
        return bool(flags & ignored_flag)
    except Exception:
        # If status_file fails for any reason, assume not ignored
        return False


def _should_skip(
    candidate_path: str,
    repo_root: str,
    respect_gitignore: bool,
) -> bool:
    """Return True if candidate should be excluded based on flags."""
    if respect_gitignore and _is_ignored(candidate_path, repo_root):
        return True
    return False


from omniagents.builtin.tools._workspace import (
    workspace_root_from_ctx as _workspace_root_from_ctx,
)

# Constants for list_directory
_LIST_DIR_DEFAULT_LIMIT = 50
_LIST_DIR_DEFAULT_DEPTH = 2
_LIST_DIR_MAX_ENTRY_LENGTH = 500
_LIST_DIR_INDENT_SPACES = 2


def _truncate_entry_name(
    name: str, max_length: int = _LIST_DIR_MAX_ENTRY_LENGTH
) -> str:
    """Truncate an entry name to max_length, preserving UTF-8 char boundaries."""
    if len(name) <= max_length:
        return name
    return (
        name[:max_length]
        .encode("utf-8", errors="ignore")[:max_length]
        .decode("utf-8", errors="ignore")
    )


class _DirEntry:
    """Represents a directory entry with metadata for display."""

    def __init__(
        self,
        name: str,
        display_name: str,
        full_path: str,
        depth: int,
        entry_type: str,  # "dir", "file", "symlink", "other"
    ):
        self.name = name  # For sorting
        self.display_name = display_name
        self.full_path = full_path
        self.depth = depth
        self.entry_type = entry_type

    def format_line(self) -> str:
        """Format entry with indentation and type suffix."""
        indent = " " * (self.depth * _LIST_DIR_INDENT_SPACES)
        suffix = ""
        if self.entry_type == "dir":
            suffix = "/"
        elif self.entry_type == "symlink":
            suffix = "@"
        elif self.entry_type == "other":
            suffix = "?"
        return f"{indent}{self.display_name}{suffix}"


async def _collect_entries_with_depth(
    dir_path: str,
    base_path: str,
    current_depth: int,
    max_depth: int,
    respect_gitignore: bool,
) -> List[_DirEntry]:
    """
    Collect directory entries up to max_depth levels deep.
    Returns entries in sorted order with proper nesting. Routes filesystem
    reads through the workspace contextvar so the same body works in host
    and sandbox modes.
    """
    from omniagents import workspace as _ws

    entries: List[_DirEntry] = []

    try:
        items = await _ws.list_dir(
            dir_path,
            include_hidden=True,
            respect_gitignore=False,
        )
    except PermissionError:
        return entries
    except Exception:
        return entries

    # workspace.list_dir already sorts by name; resort defensively.
    items.sort(key=lambda e: e.name)

    for item in items:
        full_path = _ws.resolve(item.path)

        # Check if should skip
        if _should_skip(full_path, base_path, respect_gitignore):
            continue

        # Determine entry type from the workspace's FileEntry view.
        if item.is_symlink:
            entry_type = "symlink"
        elif item.is_dir:
            entry_type = "dir"
        else:
            # Files (and unknown types) fall through to "file"; the host
            # workspace doesn't surface block/char/socket/fifo specifically.
            entry_type = "file"

        # Create display name (truncated if needed)
        display_name = _truncate_entry_name(item.name)

        entry = _DirEntry(
            name=item.name,
            display_name=display_name,
            full_path=full_path,
            depth=current_depth,
            entry_type=entry_type,
        )
        entries.append(entry)

        # Recurse into directories if within depth limit
        if entry_type == "dir" and current_depth < max_depth - 1:
            child_entries = await _collect_entries_with_depth(
                full_path,
                base_path,
                current_depth + 1,
                max_depth,
                respect_gitignore,
            )
            entries.extend(child_entries)

    return entries


@rich_function_tool(running_summary="Listing directory...")
async def list_directory(
    ctx: RunContextWrapper[Any],
    path: str,
    offset: Optional[int] = None,
    limit: Optional[int] = None,
    depth: Optional[int] = None,
    respect_gitignore: Optional[bool] = None,
) -> RichToolOutput:
    """
    Lists files and directories in the specified path with pagination and depth control.

    Args:
        path: The directory path to list contents of
        offset: 1-based entry number to start from (default: 1)
        limit: Maximum number of entries to return (default: 50)
        depth: Maximum directory depth to traverse (default: 2, min: 1)
        respect_gitignore: If True, exclude paths ignored by Git (default: True)

    Returns:
        Tuple of (llm_output, ui_metadata) where:
        - llm_output: Indented tree with type markers (/ for dirs, @ for symlinks)
        - ui_metadata: Rich metadata for UI rendering
        Hidden files (dotfiles) are always included; in coding workspaces
        they're typically relevant (config, hooks, dotfile bundles).

    Usage:
        # List current directory (default: 50 entries, 2 levels deep)
        list_directory(".")

        # List with pagination
        list_directory(".", offset=51, limit=50)

        # List 3 levels deep
        list_directory(".", depth=3)

    Output format:
        Absolute path: /path/to/dir
        src/
          components/
            Button.tsx
            Input.tsx
          utils.ts
        README.md
        [25 more entries. Use offset=26 to continue.]

    Notes:
        - Entries are sorted alphabetically
        - Directories show nested contents up to the specified depth
        - Symlinks are marked with @, directories with /
        - Entry names longer than 500 characters are truncated
    """
    try:
        # Set defaults
        if offset is None:
            offset = 1
        if offset < 1:
            offset = 1
        if limit is None:
            limit = _LIST_DIR_DEFAULT_LIMIT
        if limit < 1:
            limit = 1
        if depth is None:
            depth = _LIST_DIR_DEFAULT_DEPTH
        if depth < 1:
            depth = 1
        if respect_gitignore is None:
            respect_gitignore = True

        from omniagents import workspace as _ws

        path = _ws.resolve(path)

        if not await _ws.is_dir(path):
            return RichToolOutput(
                f"Error: '{path}' is not a directory",
                {
                    "value": "",
                    "display_type": "error",
                    "summary": "Not a directory",
                    "preview": "",
                    "truncated": False,
                    "metadata": {"error_type": "not_directory", "path": path},
                },
            )

        # Collect all entries with depth-limited traversal
        all_entries = await _collect_entries_with_depth(
            path, path, 0, depth, respect_gitignore
        )

        total_entries = len(all_entries)

        if total_entries == 0:
            return RichToolOutput(
                f"Absolute path: {path}\n(empty directory)",
                {
                    "value": {"entries": []},
                    "display_type": "directory_listing",
                    "summary": f"Directory {os.path.basename(path)} is empty",
                    "preview": "(empty)",
                    "truncated": False,
                    "metadata": {
                        "path": path,
                        "total_entries": 0,
                        "entries_returned": 0,
                        "depth": depth,
                        "respect_gitignore": respect_gitignore,
                    },
                },
            )

        # Apply pagination
        start_idx = offset - 1
        if start_idx >= total_entries:
            return RichToolOutput(
                f"Error: offset {offset} exceeds entry count ({total_entries} entries)",
                {
                    "value": "",
                    "display_type": "error",
                    "summary": "Offset out of range",
                    "preview": "",
                    "truncated": False,
                    "metadata": {"error_type": "invalid_offset", "path": path},
                },
            )

        end_idx = min(start_idx + limit, total_entries)
        selected_entries = all_entries[start_idx:end_idx]

        # Format output lines
        output_lines = [f"Absolute path: {path}"]
        for entry in selected_entries:
            output_lines.append(entry.format_line())

        # Add truncation notice if there are more entries
        remaining = total_entries - end_idx
        was_truncated = remaining > 0
        if was_truncated:
            next_offset = end_idx + 1
            output_lines.append(
                f"[{remaining} more entries. Use offset={next_offset} to continue.]"
            )

        llm_output = "\n".join(output_lines)

        # Create preview for UI (first 20 formatted entries)
        preview_limit = 20
        preview_entries = selected_entries[:preview_limit]
        preview_lines = [entry.format_line() for entry in preview_entries]
        if len(selected_entries) > preview_limit:
            preview_lines.append(
                f"... and {len(selected_entries) - preview_limit} more"
            )
        preview = "\n".join(preview_lines) if preview_lines else "(empty)"

        # Count dirs and files in selection
        dir_count = sum(1 for e in selected_entries if e.entry_type == "dir")
        file_count = sum(1 for e in selected_entries if e.entry_type == "file")
        symlink_count = sum(1 for e in selected_entries if e.entry_type == "symlink")

        # Create UI metadata
        ui_metadata = {
            "value": {
                "entries": [
                    {
                        "name": e.display_name,
                        "path": e.full_path,
                        "type": e.entry_type,
                        "depth": e.depth,
                    }
                    for e in selected_entries
                ]
            },
            "display_type": "directory_listing",
            "summary": f"Listed {len(selected_entries)} of {total_entries} entries in {os.path.basename(path)}",
            "preview": preview,
            "truncated": was_truncated or len(selected_entries) > preview_limit,
            "metadata": {
                "path": path,
                "total_entries": total_entries,
                "entries_returned": len(selected_entries),
                "offset": offset,
                "limit": limit,
                "depth": depth,
                "dir_count": dir_count,
                "file_count": file_count,
                "symlink_count": symlink_count,
                "was_truncated": was_truncated,
                "respect_gitignore": respect_gitignore,
            },
        }

        return RichToolOutput(llm_output, ui_metadata)

    except Exception as e:
        llm_output = f"Error listing directory: {str(e)}"

        ui_metadata = {
            "value": f"Error listing directory: {str(e)}",
            "display_type": "error",
            "summary": "Directory listing failed",
            "preview": str(e),
            "truncated": False,
            "metadata": {
                "error_type": "directory_error",
                "path": path,
                "respect_gitignore": (
                    bool(respect_gitignore) if respect_gitignore is not None else None
                ),
            },
        }

        return RichToolOutput(llm_output, ui_metadata)


# Constants for read_file
_READ_FILE_DEFAULT_LIMIT = 2000
_READ_FILE_MAX_LINE_LENGTH = 500
_READ_FILE_TAB_WIDTH = 4
_READ_FILE_COMMENT_PREFIXES = ("#", "//", "--", "/*", "<!--")


def _truncate_line(line: str, max_length: int = _READ_FILE_MAX_LINE_LENGTH) -> str:
    """Truncate a line to max_length, preserving UTF-8 char boundaries."""
    if len(line) <= max_length:
        return line
    # Find a safe truncation point at a character boundary
    truncated = line[:max_length]
    # Ensure we don't cut in the middle of a multi-byte character
    return truncated.encode("utf-8", errors="ignore")[:max_length].decode(
        "utf-8", errors="ignore"
    )


def _measure_indent(line: str, tab_width: int = _READ_FILE_TAB_WIDTH) -> int:
    """Measure indentation level in spaces (tabs converted to tab_width spaces)."""
    indent = 0
    for char in line:
        if char == " ":
            indent += 1
        elif char == "\t":
            indent += tab_width
        else:
            break
    return indent


def _is_comment_line(line: str) -> bool:
    """Check if a line is a comment (for header detection in indentation mode)."""
    stripped = line.strip()
    return any(stripped.startswith(prefix) for prefix in _READ_FILE_COMMENT_PREFIXES)


def _read_file_indentation_mode(
    all_lines: List[str],
    anchor_line: int,
    max_levels: int,
    include_siblings: bool,
    include_header: bool,
    max_lines: int,
) -> Tuple[List[Tuple[int, str]], bool]:
    """
    Read lines around an anchor using indentation-aware expansion.

    Returns a list of (line_number, line_content) tuples and a flag indicating
    if the output was truncated due to max_lines.
    """
    if not all_lines:
        return [], False

    total_lines = len(all_lines)
    if anchor_line < 1 or anchor_line > total_lines:
        return [], False

    anchor_idx = anchor_line - 1

    # Compute effective indents (blank lines inherit previous indent)
    effective_indents = []
    prev_indent = 0
    for line in all_lines:
        if line.strip():
            prev_indent = _measure_indent(line)
        effective_indents.append(prev_indent)

    anchor_indent = effective_indents[anchor_idx]

    # Compute minimum indent based on max_levels
    if max_levels == 0:
        min_indent = 0
    else:
        min_indent = max(0, anchor_indent - (max_levels * _READ_FILE_TAB_WIDTH))

    # Expand outward from anchor
    result_indices = [anchor_idx]
    up_idx = anchor_idx - 1
    down_idx = anchor_idx + 1
    up_min_indent_count = 0
    down_min_indent_count = 0

    while len(result_indices) < max_lines:
        progressed = False

        # Expand upward
        if up_idx >= 0:
            if effective_indents[up_idx] >= min_indent:
                # Check sibling/header rules at min_indent level
                if effective_indents[up_idx] == min_indent and not include_siblings:
                    allow_header = include_header and _is_comment_line(
                        all_lines[up_idx]
                    )
                    can_take = allow_header or up_min_indent_count == 0
                    if can_take:
                        result_indices.insert(0, up_idx)
                        up_min_indent_count += 1
                        progressed = True
                    up_idx = -1  # Stop going up
                else:
                    result_indices.insert(0, up_idx)
                    progressed = True
                up_idx -= 1
            else:
                up_idx = -1  # Hit a line with less indent, stop

        if len(result_indices) >= max_lines:
            break

        # Expand downward
        if down_idx < total_lines:
            if effective_indents[down_idx] >= min_indent:
                if effective_indents[down_idx] == min_indent and not include_siblings:
                    if down_min_indent_count > 0:
                        down_idx = total_lines  # Stop going down
                    else:
                        result_indices.append(down_idx)
                        down_min_indent_count += 1
                        progressed = True
                        down_idx += 1
                else:
                    result_indices.append(down_idx)
                    progressed = True
                    down_idx += 1
            else:
                down_idx = total_lines  # Hit a line with less indent, stop

        if not progressed:
            break

    # Trim leading/trailing blank lines
    while result_indices and not all_lines[result_indices[0]].strip():
        result_indices.pop(0)
    while result_indices and not all_lines[result_indices[-1]].strip():
        result_indices.pop()

    truncated = len(result_indices) >= max_lines
    return [(idx + 1, all_lines[idx]) for idx in result_indices], truncated


@rich_function_tool(running_summary="Reading file...")
async def read_file(
    ctx: RunContextWrapper[Any],
    file_path: str,
    start_line: Optional[int] = None,
    num_lines: Optional[int] = None,
    mode: Optional[str] = None,
    anchor_line: Optional[int] = None,
    max_levels: Optional[int] = None,
    include_siblings: Optional[bool] = None,
    include_header: Optional[bool] = None,
) -> RichToolOutput:
    """
    Reads the contents of a file with line numbers and truncation handling.

    Args:
        file_path: Path to the file to read
        start_line: 1-based line number to start reading from (default: 1)
        num_lines: Maximum number of lines to read (default: 2000)
        mode: Reading mode - "slice" (default) or "indentation"
        anchor_line: For indentation mode: the line to expand around (defaults to start_line)
        max_levels: For indentation mode: max parent indentation levels to include (0 = unlimited)
        include_siblings: For indentation mode: include sibling blocks at same indent (default: False)
        include_header: For indentation mode: include header comments above blocks (default: True)

    Returns:
        Tuple of (llm_output, ui_metadata) where:
        - llm_output: Lines formatted as "L{n}: {content}" with truncation notices
        - ui_metadata: Rich metadata for UI rendering

    Usage:
        # Read first 2000 lines of a file (default)
        read_file("/path/to/file.txt")

        # Read 50 lines starting from line 100
        read_file("/path/to/file.txt", start_line=100, num_lines=50)

        # Read the function/block containing line 42 (indentation mode)
        read_file("/path/to/file.py", mode="indentation", anchor_line=42, max_levels=2)

    Notes:
        - Lines are prefixed with "L{n}: " for easy reference
        - Lines longer than 500 characters are truncated
        - In indentation mode, the tool expands from anchor_line to capture the
          logical code block based on indentation levels
    """
    try:
        # Set defaults
        if start_line is None:
            start_line = 1
        if start_line < 1:
            start_line = 1
        if num_lines is None:
            num_lines = _READ_FILE_DEFAULT_LIMIT
        if mode is None:
            mode = "slice"
        if max_levels is None:
            max_levels = 0
        if include_siblings is None:
            include_siblings = False
        if include_header is None:
            include_header = True

        from omniagents import workspace as _ws

        text = await _ws.read_text(file_path)
        # Match the prior ``f.readlines()`` behavior: each line retains its
        # trailing newline (if any), and a no-final-newline file produces a
        # final tail line without one. ``splitlines(keepends=True)`` matches
        # exactly.
        all_lines = text.splitlines(keepends=True)

        # Resolve once for display / metadata purposes. Falls back to the
        # input path if resolution fails (e.g. tests with stubbed workspaces).
        try:
            file_path = _ws.resolve(file_path)
        except Exception:
            pass

        total_file_lines = len(all_lines)
        truncation_notices = []
        was_truncated = False

        if mode == "indentation":
            # Indentation-aware reading
            effective_anchor = anchor_line if anchor_line is not None else start_line
            max_result_lines = min(num_lines, _READ_FILE_DEFAULT_LIMIT)

            line_tuples, indent_truncated = _read_file_indentation_mode(
                all_lines,
                effective_anchor,
                max_levels,
                include_siblings,
                include_header,
                max_result_lines,
            )

            if not line_tuples:
                return RichToolOutput(
                    f"Error: anchor_line {effective_anchor} is out of range (file has {total_file_lines} lines)",
                    {
                        "value": "",
                        "display_type": "error",
                        "summary": "Invalid anchor line",
                        "preview": "",
                        "truncated": False,
                        "metadata": {
                            "error_type": "invalid_anchor",
                            "file_path": file_path,
                        },
                    },
                )

            was_truncated = indent_truncated
            selected_line_numbers = [ln for ln, _ in line_tuples]
            selected_lines = [content for _, content in line_tuples]

        else:
            # Slice mode (default)
            start_idx = start_line - 1

            if start_idx >= total_file_lines:
                return RichToolOutput(
                    f"Error: start_line {start_line} exceeds file length ({total_file_lines} lines)",
                    {
                        "value": "",
                        "display_type": "error",
                        "summary": "Start line out of range",
                        "preview": "",
                        "truncated": False,
                        "metadata": {
                            "error_type": "invalid_offset",
                            "file_path": file_path,
                        },
                    },
                )

            end_idx = min(start_idx + num_lines, total_file_lines)
            selected_lines = all_lines[start_idx:end_idx]
            selected_line_numbers = list(
                range(start_line, start_line + len(selected_lines))
            )

            # Check if we hit the limit
            remaining_lines = total_file_lines - end_idx
            if remaining_lines > 0 and len(selected_lines) >= num_lines:
                was_truncated = True
                truncation_notices.append(
                    f"[{remaining_lines} more lines not shown. Use start_line={end_idx + 1} to continue.]"
                )

        # Format lines with line numbers and truncate long lines
        formatted_lines = []
        lines_truncated_count = 0

        for line_num, line in zip(selected_line_numbers, selected_lines):
            # Remove trailing newline for processing
            line_content = line.rstrip("\n\r")

            # Truncate long lines
            if len(line_content) > _READ_FILE_MAX_LINE_LENGTH:
                original_len = len(line_content)
                line_content = _truncate_line(line_content, _READ_FILE_MAX_LINE_LENGTH)
                lines_truncated_count += 1

            formatted_lines.append(f"L{line_num}: {line_content}")

        # Build LLM output with truncation notices
        llm_lines = []

        # Add header with truncation info if needed
        if was_truncated or lines_truncated_count > 0:
            llm_lines.append(f"Total file lines: {total_file_lines}")
            if lines_truncated_count > 0:
                llm_lines.append(
                    f"[{lines_truncated_count} lines exceeded {_READ_FILE_MAX_LINE_LENGTH} chars and were truncated]"
                )
            llm_lines.append("")

        llm_lines.extend(formatted_lines)

        if truncation_notices:
            llm_lines.append("")
            llm_lines.extend(truncation_notices)

        llm_output = "\n".join(llm_lines)

        # Detect file language from extension
        ext = os.path.splitext(file_path)[1].lower()
        language_map = {
            ".py": "python",
            ".js": "javascript",
            ".ts": "typescript",
            ".tsx": "typescript",
            ".jsx": "javascript",
            ".java": "java",
            ".cpp": "cpp",
            ".c": "c",
            ".h": "c",
            ".hpp": "cpp",
            ".cs": "csharp",
            ".go": "go",
            ".rs": "rust",
            ".rb": "ruby",
            ".php": "php",
            ".swift": "swift",
            ".kt": "kotlin",
            ".scala": "scala",
            ".html": "html",
            ".css": "css",
            ".scss": "scss",
            ".json": "json",
            ".xml": "xml",
            ".yaml": "yaml",
            ".yml": "yaml",
            ".toml": "toml",
            ".md": "markdown",
            ".sh": "bash",
            ".bash": "bash",
            ".zsh": "zsh",
            ".sql": "sql",
        }
        language = language_map.get(ext, "text")

        # Create preview for UI (first 20 formatted lines)
        preview_limit = 20
        preview_lines = formatted_lines[:preview_limit]
        preview = "\n".join(preview_lines)
        ui_truncated = len(formatted_lines) > preview_limit

        # Create UI metadata
        ui_metadata = {
            "value": "\n".join(formatted_lines),  # The formatted file content
            "display_type": "file_content",
            "summary": f"Read {len(formatted_lines)} lines from {os.path.basename(file_path)}",
            "preview": preview,
            "truncated": ui_truncated or was_truncated,
            "metadata": {
                "file_path": file_path,
                "total_file_lines": total_file_lines,
                "lines_returned": len(formatted_lines),
                "lines_truncated": lines_truncated_count,
                "language": language,
                "start_line": (
                    selected_line_numbers[0] if selected_line_numbers else start_line
                ),
                "end_line": (
                    selected_line_numbers[-1] if selected_line_numbers else start_line
                ),
                "mode": mode,
                "was_content_truncated": was_truncated,
                "preview_lines": min(len(formatted_lines), preview_limit),
            },
        }

        return RichToolOutput(llm_output, ui_metadata)

    except Exception as e:
        llm_output = f"Error reading file: {str(e)}"

        ui_metadata = {
            "value": f"Error reading file: {str(e)}",
            "display_type": "error",
            "summary": "File read failed",
            "preview": str(e),
            "truncated": False,
            "metadata": {"error_type": "read_error", "file_path": file_path},
        }

        return RichToolOutput(llm_output, ui_metadata)


@rich_function_tool(running_summary="Applying edits...")
async def edit_file(
    ctx: RunContextWrapper[Any],
    file_path: str,
    old_text: str,
    new_text: str,
    expected_replacements: Optional[int] = None,
    max_replacements: Optional[int] = None,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
    dry_run: Optional[bool] = False,
    regex: Optional[bool] = False,
) -> RichToolOutput:
    """
    Makes targeted edits to a specific file by replacing text.

    Args:
        file_path: Path to the file to edit
        old_text: The text to replace. By default, treated as a literal string.
        new_text: The replacement text.
        expected_replacements: If provided, the edit will fail unless exactly this many replacements occur.
        max_replacements: If provided, limit the number of replacements (applies to the selected range or entire file).
        start_line: 1-based start line to restrict replacements to a range (inclusive). If None, start at first line.
        end_line: 1-based end line to restrict replacements to a range (inclusive). If None, end at last line.
        dry_run: If True, do not write the file — return a diff preview of the proposed changes.
        regex: If True, interpret old_text as a regular expression (not escaped).

    Returns:
        Tuple of (llm_output, ui_metadata) where:
        - llm_output: Description of edit result
        - ui_metadata: Rich metadata for UI rendering, including a unified diff preview

    Usage:
        Use this tool to make specific text replacements in files.

    Caveats:
        - Escapes vs newlines in code: If you intend to insert a literal backslash-n in a string (e.g. Go/Java/C),
          pass the two characters backslash and n (written as "\\n"). Using a newline escape inserts an actual
          newline character into the file.
        - Literal vs regex: old_text is treated literally unless regex=True. For multi-line literals, include actual
          newline characters in old_text; for inserting the literal two-character sequence backslash-n, use "\\n".
        - Range semantics: start_line/end_line are 1-based and inclusive. Lines outside the range are left unchanged.

    Examples:
        # Replace a function name in a Python file
        edit_file("/path/to/file.py", "def old_function()", "def new_function()")

        # Replace a configuration value
        edit_file("/path/to/config.json", '"debug": false', '"debug": true')

        # Regex replacement in a specific range, dry run with expected count
        edit_file(
            "/path/to/file.go",
            old_text=r"fmt\\.Fprintf\\(.*\\)",
            new_text="fmt.Fprintln(w, msg)",
            regex=True,
            start_line=100,
            end_line=140,
            expected_replacements=1,
            dry_run=True,
        )
    """
    from omniagents import workspace as _ws

    try:
        # Read existing content via the workspace so the same body works in
        # host and sandbox modes. Resolve to an absolute path for display /
        # metadata fields that the existing tool exposes.
        content = await _ws.read_text(file_path)
        try:
            file_path = _ws.resolve(file_path)
        except Exception:
            pass

        # Prepare target window (optional line range)
        lines = content.splitlines(keepends=True)
        total_lines = len(lines)
        s_idx = 0 if start_line is None else max(0, min(total_lines, start_line - 1))
        e_idx = total_lines if end_line is None else max(0, min(total_lines, end_line))
        if start_line is not None and end_line is not None and s_idx > e_idx:
            raise ValueError("start_line must be <= end_line")

        # Build search pattern
        pattern = old_text if regex else re.escape(old_text)
        count = 0 if max_replacements is None else max_replacements

        # Apply replacement to the selected slice or whole content
        if s_idx == 0 and e_idx == total_lines:
            new_content, replacements = re.subn(
                pattern, new_text, content, count=count, flags=re.DOTALL | re.MULTILINE
            )
        else:
            prefix = "".join(lines[:s_idx])
            target = "".join(lines[s_idx:e_idx])
            suffix = "".join(lines[e_idx:])
            replaced, replacements = re.subn(
                pattern, new_text, target, count=count, flags=re.DOTALL | re.MULTILINE
            )
            new_content = prefix + replaced + suffix

        # Guard: expected replacements
        if expected_replacements is not None and replacements != expected_replacements:
            # Diff for visibility even on failure
            diff_lines = list(
                difflib.unified_diff(
                    content.splitlines(keepends=True),
                    new_content.splitlines(keepends=True),
                    fromfile=file_path,
                    tofile=file_path,
                    lineterm="",
                    n=3,
                )
            )
            preview = "".join(diff_lines[:40]) if diff_lines else "No changes"
            llm_output = f"Edit aborted: expected {expected_replacements} replacement(s), got {replacements}."
            ui_metadata = {
                "display_type": "diff",
                "summary": f"Edit aborted for {os.path.basename(file_path)}",
                "value": {
                    "diff_lines": diff_lines,
                    "diff_text": "".join(diff_lines),
                },
                "preview": preview,
                "truncated": len(diff_lines) > 40,
                "metadata": {
                    "file_path": file_path,
                    "operation": "edit",
                    "expected_replacements": expected_replacements,
                    "actual_replacements": replacements,
                    "range": {
                        "start_line": start_line,
                        "end_line": end_line,
                    },
                    "regex": bool(regex),
                    "dry_run": bool(dry_run),
                },
            }
            return RichToolOutput(llm_output, ui_metadata)

        # Build diff preview
        diff_lines = list(
            difflib.unified_diff(
                content.splitlines(keepends=True),
                new_content.splitlines(keepends=True),
                fromfile=file_path,
                tofile=file_path,
                lineterm="",
                n=3,
            )
        )
        preview = "".join(diff_lines[:80]) if diff_lines else "No changes"
        additions = sum(
            1 for l in diff_lines if l.startswith("+") and not l.startswith("+++")
        )
        deletions = sum(
            1 for l in diff_lines if l.startswith("-") and not l.startswith("---")
        )

        if dry_run:
            llm_output = f"Dry run: would update {os.path.basename(file_path)} - {replacements} replacement(s)"
            ui_metadata = {
                "display_type": "diff",
                "summary": f"Dry run edit for {os.path.basename(file_path)} ({replacements} replacements)",
                "value": {
                    "diff_lines": diff_lines,
                    "diff_text": "".join(diff_lines),
                },
                "preview": preview,
                "truncated": len(diff_lines) > 80,
                "metadata": {
                    "file_path": file_path,
                    "operation": "edit_dry_run",
                    "changes_made": replacements,
                    "replacements": replacements,
                    "additions": additions,
                    "deletions": deletions,
                    "range": {
                        "start_line": start_line,
                        "end_line": end_line,
                    },
                    "regex": bool(regex),
                },
            }
            return RichToolOutput(llm_output, ui_metadata)

        # If no actual changes
        if replacements == 0:
            llm_output = f"No matches found for the specified text in {file_path}"
            ui_metadata = {
                "value": "No matches found for the specified text",
                "display_type": "error",
                "summary": "Edit failed - text not found",
                "preview": "No matches found for the specified text",
                "truncated": False,
                "metadata": {
                    "error_type": "no_match",
                    "file_path": file_path,
                },
            }
            return RichToolOutput(llm_output, ui_metadata)

        # Write the modified content back via the workspace
        await _ws.write_text(file_path, new_content)

        # LLM output
        llm_output = f"Successfully updated {os.path.basename(file_path)} - made {replacements} replacement(s)"

        # UI metadata
        ui_metadata = {
            "display_type": "diff",
            "summary": f"Updated {os.path.basename(file_path)} ({replacements} replacements)",
            "value": {
                "diff_lines": diff_lines,
                "diff_text": "".join(diff_lines),
            },
            "preview": preview,
            "truncated": len(diff_lines) > 80,
            "metadata": {
                "file_path": file_path,
                "operation": "edit",
                "changes_made": replacements,
                "replacements": replacements,
                "additions": additions,
                "deletions": deletions,
                "range": {
                    "start_line": start_line,
                    "end_line": end_line,
                },
                "regex": bool(regex),
            },
        }

        return RichToolOutput(llm_output, ui_metadata)
    except Exception as e:
        llm_output = f"Error editing file: {str(e)}"

        ui_metadata = {
            "value": f"Error editing file: {str(e)}",
            "display_type": "error",
            "summary": "File edit failed",
            "preview": str(e),
            "truncated": False,
            "metadata": {"error_type": "edit_error", "file_path": file_path},
        }

        return RichToolOutput(llm_output, ui_metadata)


@rich_function_tool(running_summary="Writing file...")
async def write_file(
    ctx: RunContextWrapper[Any], file_path: str, content: str, append: Optional[bool]
) -> RichToolOutput:
    """
    Creates a new file or overwrites an existing file with the provided content.

    Args:
        file_path: Path to the file to write
        content: Content to write to the file
        append: Whether to append to the file instead of overwriting

    Returns:
        Tuple of (llm_output, ui_metadata) where:
        - llm_output: Description of write result
        - ui_metadata: Rich metadata for UI rendering

    Usage:
        Use this tool to create or modify files.
        - If append is not provided, it defaults to False (overwrite mode)

    Examples:
        # Create a new Python file
        write_file("/path/to/new_file.py", "def hello_world():\\n    print('Hello, world!')", append=False)

        # Append to a log file
        write_file("/path/to/log.txt", "New log entry\\n", append=True)
    """
    from omniagents import workspace as _ws

    try:
        # Default to overwrite if not specified
        if append is None:
            append = False

        operation = "append" if append else "write"

        await _ws.write_text(file_path, content, append=append)

        # Resolve for display so the summary/metadata reflect the
        # workspace-relative absolute path even when the caller passed
        # a relative one.
        try:
            resolved_path = _ws.resolve(file_path)
        except Exception:
            resolved_path = file_path

        bytes_written = len(content)

        # Create preview of written content
        lines = content.split("\n")
        preview_limit = 10
        if len(lines) > preview_limit:
            preview = "\n".join(lines[:preview_limit])
            truncated = True
        else:
            preview = content[:500]  # Limit preview to 500 chars
            truncated = len(content) > 500

        # LLM output
        llm_output = f"Successfully {operation} {os.path.basename(resolved_path)} ({bytes_written} bytes written)"

        # UI metadata
        ui_metadata = {
            "value": f"{bytes_written} bytes written",  # The actual result
            "display_type": "file_edit",
            "summary": f"{operation.capitalize()} {os.path.basename(resolved_path)} ({bytes_written} bytes)",
            "preview": preview,
            "truncated": truncated,
            "metadata": {
                "file_path": resolved_path,
                "operation": operation,
                "bytes_written": bytes_written,
                "line_count": len(lines),
            },
        }

        return RichToolOutput(llm_output, ui_metadata)
    except Exception as e:
        llm_output = f"Error writing file: {str(e)}"

        ui_metadata = {
            "value": f"Error writing file: {str(e)}",
            "display_type": "error",
            "summary": "File write failed",
            "preview": str(e),
            "truncated": False,
            "metadata": {"error_type": "write_error", "file_path": file_path},
        }

        return RichToolOutput(llm_output, ui_metadata)


# --- Approval Metadata Generators --- #
# These functions generate rich metadata for tool approvals to enhance the UI


@approval_metadata()
def edit_file_approval(
    file_path: str,
    old_text: str,
    new_text: str,
    expected_replacements: Optional[int] = None,
    max_replacements: Optional[int] = None,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
    dry_run: Optional[bool] = False,
    regex: Optional[bool] = False,
    **kwargs,
) -> dict:
    """Generate approval metadata for edit_file tool.

    Attempts to preview the actual file diff by applying the proposed change in-memory.
    Falls back to showing an old_text/new_text diff if the file cannot be read.
    """

    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        lines = content.splitlines(keepends=True)
        total = len(lines)
        s_idx = 0 if start_line is None else max(0, min(total, start_line - 1))
        e_idx = total if end_line is None else max(0, min(total, end_line))
        pattern = old_text if regex else re.escape(old_text)
        count = 0 if max_replacements is None else max_replacements
        if s_idx == 0 and e_idx == total:
            new_content, repl = re.subn(
                pattern, new_text, content, count=count, flags=re.DOTALL | re.MULTILINE
            )
        else:
            prefix = "".join(lines[:s_idx])
            target = "".join(lines[s_idx:e_idx])
            suffix = "".join(lines[e_idx:])
            replaced, repl = re.subn(
                pattern, new_text, target, count=count, flags=re.DOTALL | re.MULTILINE
            )
            new_content = prefix + replaced + suffix
        diff_lines = list(
            difflib.unified_diff(
                content.splitlines(keepends=True),
                new_content.splitlines(keepends=True),
                fromfile=file_path,
                tofile=file_path,
                lineterm="",
                n=3,
            )
        )
    except Exception:
        # Fallback: diff between old_text and new_text parameters
        diff_lines = list(
            difflib.unified_diff(
                old_text.splitlines(keepends=True),
                new_text.splitlines(keepends=True),
                fromfile=file_path,
                tofile=file_path,
                lineterm="",
                n=3,
            )
        )

    preview_lines = diff_lines[:20] if diff_lines else ["No changes"]
    preview = "".join(preview_lines)
    additions = sum(
        1 for l in diff_lines if l.startswith("+") and not l.startswith("+++")
    )
    deletions = sum(
        1 for l in diff_lines if l.startswith("-") and not l.startswith("---")
    )

    return {
        "display_type": "diff",  # Tell UI to use diff renderer
        "summary": f"Edit {os.path.basename(file_path) if file_path else 'file'}",
        "value": {"old_text": old_text, "new_text": new_text, "diff_lines": diff_lines},
        "preview": preview,
        "truncated": len(diff_lines) > 20,
        "metadata": {
            "file_path": file_path,
            "operation": "edit",
            "additions": additions,
            "deletions": deletions,
            "total_lines": len(diff_lines),
            "range": {"start_line": start_line, "end_line": end_line},
            "regex": bool(regex),
            "expected_replacements": expected_replacements,
            "max_replacements": max_replacements,
            "dry_run": bool(dry_run),
        },
    }


@approval_metadata()
def write_file_approval(file_path: str, content: str, append: Optional[bool]) -> dict:
    """Generate approval metadata for write_file tool."""

    # Default to overwrite if not specified
    if append is None:
        append = False

    # Try to read existing file content for diff
    old_content = ""
    file_exists = False
    try:
        if file_path and os.path.exists(file_path):
            with open(file_path, "r", encoding="utf-8") as f:
                old_content = f.read()
            file_exists = True
    except Exception:
        pass  # If we can't read it, just show preview without diff

    # If file exists and we can show a diff
    if file_exists:
        # Determine new content based on operation
        if append:
            new_content = old_content + content
        else:
            new_content = content

        # Generate unified diff
        diff_lines = list(
            difflib.unified_diff(
                old_content.splitlines(keepends=True),
                new_content.splitlines(keepends=True),
                fromfile=file_path,
                tofile=file_path,
                lineterm="",
                n=3,
            )
        )

        # Create preview from diff
        preview_lines = diff_lines[:20] if diff_lines else ["No changes"]
        preview = "".join(preview_lines)

        # Count additions and deletions
        additions = sum(
            1 for l in diff_lines if l.startswith("+") and not l.startswith("+++")
        )
        deletions = sum(
            1 for l in diff_lines if l.startswith("-") and not l.startswith("---")
        )

        return {
            "display_type": "diff",  # Use diff display for existing files
            "summary": f"{'Append to' if append else 'Overwrite'} {os.path.basename(file_path)}",
            "value": {
                "old_text": old_content[:1000],  # Truncate for memory
                "new_text": new_content[:1000],
                "diff_lines": diff_lines,
            },
            "preview": preview,
            "truncated": len(diff_lines) > 20,
            "metadata": {
                "file_path": file_path,
                "operation": "append" if append else "overwrite",
                "additions": additions,
                "deletions": deletions,
                "total_lines": len(diff_lines),
                "file_exists": True,
            },
        }
    else:
        # New file creation - show preview without diff
        lines = content.splitlines()
        preview_lines = lines[:10]
        preview = "\n".join(preview_lines)

        return {
            "display_type": "file_write",
            "summary": f"Create {os.path.basename(file_path)}",
            "value": content[:1000],  # Truncated content
            "preview": preview,
            "truncated": len(content) > 1000 or len(lines) > 10,
            "metadata": {
                "file_path": file_path,
                "operation": "create",
                "size_bytes": len(content),
                "line_count": len(lines),
                "file_exists": False,
            },
        }


# ==================== CODEX APPLY_PATCH IMPLEMENTATION ====================


# Patch parsing types
class Hunk:
    """Represents a single file operation in a patch"""

    def __init__(self, path: str):
        self.path = path


class AddFile(Hunk):
    def __init__(self, path: str, contents: str):
        super().__init__(path)
        self.contents = contents


class DeleteFile(Hunk):
    def __init__(self, path: str):
        super().__init__(path)


class UpdateFile(Hunk):
    def __init__(
        self,
        path: str,
        chunks: List["UpdateFileChunk"],
        move_path: Optional[str] = None,
    ):
        super().__init__(path)
        self.move_path = move_path
        self.chunks = chunks


class UpdateFileChunk:
    """Represents a chunk of changes within an UpdateFile hunk"""

    def __init__(
        self,
        old_lines: List[str],
        new_lines: List[str],
        context_headers: List[str] = None,
        is_end_of_file: bool = False,
    ):
        self.old_lines = old_lines
        self.new_lines = new_lines
        self.context_headers = context_headers or []
        self.is_end_of_file = is_end_of_file


def _resolve_patch_path(path: Optional[str], base_dir: Optional[str]) -> Optional[str]:
    if path is None:
        return None
    if os.path.isabs(path):
        return path
    if base_dir:
        return os.path.abspath(os.path.join(base_dir, path))
    return os.path.abspath(path)


def parse_patch(patch_text: str, base_dir: Optional[str] = None) -> List[Hunk]:
    """
    Parse Codex's custom patch format into Hunk objects.

    Format:
    *** Begin Patch
    *** Add File: path
    +content lines
    *** Update File: path
    *** Move to: new_path (optional)
    @@ optional context header
    - old lines
    + new lines
      context lines (space prefix)
    *** Delete File: path
    *** End Patch
    """
    lines = patch_text.split("\n")
    hunks = []
    i = 0

    # Find start of patch
    while i < len(lines) and not lines[i].strip().startswith("*** Begin Patch"):
        i += 1

    if i >= len(lines):
        raise ValueError("Invalid patch: missing '*** Begin Patch'")

    i += 1  # Skip Begin Patch line

    while i < len(lines):
        line = lines[i].strip()

        if line.startswith("*** End Patch"):
            break

        elif line.startswith("*** Add File:"):
            path = line[len("*** Add File:") :].strip()
            i += 1
            content_lines = []

            while i < len(lines):
                if lines[i].startswith("*** "):
                    break
                if lines[i].startswith("+"):
                    content_lines.append(lines[i][1:])
                i += 1

            contents = "\n".join(content_lines)
            if content_lines:
                contents += "\n"
            resolved_path = _resolve_patch_path(path, base_dir)
            hunks.append(AddFile(resolved_path, contents))

        elif line.startswith("*** Delete File:"):
            path = line[len("*** Delete File:") :].strip()
            resolved_path = _resolve_patch_path(path, base_dir)
            hunks.append(DeleteFile(resolved_path))
            i += 1

        elif line.startswith("*** Update File:"):
            path = line[len("*** Update File:") :].strip()
            i += 1

            # Check for Move to
            move_path = None
            if i < len(lines) and lines[i].strip().startswith("*** Move to:"):
                move_path = lines[i].strip()[len("*** Move to:") :].strip()
                i += 1

            # Parse chunks
            chunks = []
            while i < len(lines):
                if lines[i].strip().startswith("*** "):
                    break

                # Parse chunk starting with @@ or plain content
                if lines[i].startswith("@@"):
                    # Context header
                    context_headers = []
                    header = lines[i][2:].strip()
                    if header:
                        context_headers.append(header)
                    i += 1

                    # Collect old and new lines for this chunk
                    old_lines = []
                    new_lines = []
                    is_end_of_file = False

                    while i < len(lines):
                        if lines[i].startswith("@@") or lines[i].strip().startswith(
                            "*** "
                        ):
                            break

                        if lines[i].strip() == "*** End of File":
                            is_end_of_file = True
                            i += 1
                            break

                        if lines[i].startswith("-"):
                            old_lines.append(lines[i][1:])
                        elif lines[i].startswith("+"):
                            new_lines.append(lines[i][1:])
                        elif lines[i].startswith(" "):
                            # Context line - add to both
                            old_lines.append(lines[i][1:])
                            new_lines.append(lines[i][1:])
                        i += 1

                    if old_lines or new_lines:
                        chunks.append(
                            UpdateFileChunk(
                                old_lines, new_lines, context_headers, is_end_of_file
                            )
                        )
                else:
                    i += 1

            if chunks:
                resolved_path = _resolve_patch_path(path, base_dir)
                resolved_move = (
                    _resolve_patch_path(move_path, base_dir) if move_path else None
                )
                hunks.append(UpdateFile(resolved_path, chunks, resolved_move))
        else:
            i += 1

    return hunks


def seek_sequence(
    lines: List[str], pattern: List[str], start: int, eof: bool
) -> Optional[int]:
    """
    Attempt to find the sequence of pattern lines within lines beginning at or after start.
    Returns the starting index of the match or None if not found.

    Matches are attempted with decreasing strictness:
    1. Exact match
    2. Ignoring trailing whitespace
    3. Ignoring leading and trailing whitespace
    4. Unicode normalization (smart quotes to ASCII, etc.)

    When eof is True, first try starting at end-of-file.
    """
    if not pattern:
        return start

    # Pattern longer than available input - no possible match
    if len(pattern) > len(lines):
        return None

    search_start = (
        len(lines) - len(pattern) if (eof and len(lines) >= len(pattern)) else start
    )

    # Pass 1: Exact match
    for i in range(search_start, len(lines) - len(pattern) + 1):
        if lines[i : i + len(pattern)] == pattern:
            return i

    # Pass 2: Ignore trailing whitespace
    for i in range(search_start, len(lines) - len(pattern) + 1):
        match = True
        for j, pat in enumerate(pattern):
            if lines[i + j].rstrip() != pat.rstrip():
                match = False
                break
        if match:
            return i

    # Pass 3: Ignore all leading/trailing whitespace
    for i in range(search_start, len(lines) - len(pattern) + 1):
        match = True
        for j, pat in enumerate(pattern):
            if lines[i + j].strip() != pat.strip():
                match = False
                break
        if match:
            return i

    # Pass 4: Unicode normalization
    def normalize(s: str) -> str:
        """Normalize Unicode punctuation to ASCII equivalents"""
        s = s.strip()
        replacements = {
            "\u2010": "-",
            "\u2011": "-",
            "\u2012": "-",
            "\u2013": "-",  # Dashes
            "\u2014": "-",
            "\u2015": "-",
            "\u2212": "-",
            "\u2018": "'",
            "\u2019": "'",
            "\u201a": "'",
            "\u201b": "'",  # Single quotes
            "\u201c": '"',
            "\u201d": '"',
            "\u201e": '"',
            "\u201f": '"',  # Double quotes
            "\u00a0": " ",
            "\u2002": " ",
            "\u2003": " ",
            "\u2004": " ",  # Spaces
            "\u2005": " ",
            "\u2006": " ",
            "\u2007": " ",
            "\u2008": " ",
            "\u2009": " ",
            "\u200a": " ",
            "\u202f": " ",
            "\u205f": " ",
            "\u3000": " ",
        }
        for old, new in replacements.items():
            s = s.replace(old, new)
        return s

    for i in range(search_start, len(lines) - len(pattern) + 1):
        match = True
        for j, pat in enumerate(pattern):
            if normalize(lines[i + j]) != normalize(pat):
                match = False
                break
        if match:
            return i

    return None


async def apply_hunks(hunks: List[Hunk]) -> Dict[str, str]:
    """
    Apply the parsed hunks and return affected files summary.
    Returns dict with keys: 'added', 'modified', 'deleted' containing file paths.
    Routes filesystem reads and writes through the workspace contextvar so the
    same body works against host and sandbox backends.
    """
    from omniagents import workspace as _ws

    added = []
    modified = []
    deleted = []

    for hunk in hunks:
        if isinstance(hunk, AddFile):
            # workspace.write_text creates parent directories by default
            await _ws.write_text(hunk.path, hunk.contents)
            added.append(hunk.path)

        elif isinstance(hunk, DeleteFile):
            if await _ws.exists(hunk.path):
                await _ws.remove(hunk.path)
                deleted.append(hunk.path)
            else:
                raise FileNotFoundError(f"Cannot delete non-existent file: {hunk.path}")

        elif isinstance(hunk, UpdateFile):
            # Read original file via the workspace
            original = await _ws.read_text(hunk.path)

            lines = original.splitlines()

            # Compute replacements for each chunk
            replacements = []
            line_index = 0

            for chunk in hunk.chunks:
                # Find where to apply this chunk
                pattern = chunk.old_lines
                found = seek_sequence(lines, pattern, line_index, chunk.is_end_of_file)

                # Retry without trailing empty line if not found
                if found is None and pattern and pattern[-1] == "":
                    pattern = pattern[:-1]
                    new_lines = chunk.new_lines
                    if new_lines and new_lines[-1] == "":
                        new_lines = new_lines[:-1]
                    found = seek_sequence(
                        lines, pattern, line_index, chunk.is_end_of_file
                    )
                else:
                    new_lines = chunk.new_lines

                if found is not None:
                    replacements.append((found, len(pattern), new_lines))
                    line_index = found + len(pattern)
                else:
                    # Use context headers to help locate if available
                    context_hint = (
                        f" (context: {', '.join(chunk.context_headers)})"
                        if chunk.context_headers
                        else ""
                    )
                    raise ValueError(
                        f"Failed to find lines to replace in {hunk.path}{context_hint}"
                    )

            # Apply replacements in reverse order to maintain indices
            for start_idx, old_len, new_lines in reversed(replacements):
                # Remove old lines
                for _ in range(old_len):
                    if start_idx < len(lines):
                        del lines[start_idx]

                # Insert new lines
                for offset, new_line in enumerate(new_lines):
                    lines.insert(start_idx + offset, new_line)

            # Write result
            new_content = "\n".join(lines)
            if lines and not new_content.endswith("\n"):
                new_content += "\n"

            if hunk.move_path:
                # Write to new location (workspace.write_text creates parents)
                await _ws.write_text(hunk.move_path, new_content)
                # Remove original
                await _ws.remove(hunk.path)
                modified.append(hunk.move_path)
            else:
                await _ws.write_text(hunk.path, new_content)
                modified.append(hunk.path)

    return {"added": added, "modified": modified, "deleted": deleted}


@approval_metadata()
def apply_patch_approval(
    patch: str,
    **kwargs,
) -> dict:
    """Generate approval metadata for apply_patch tool."""
    # Build a unified diff for each file operation so Bubble Tea can render a proper diff view
    # If we fail to parse or generate a proper diff, we fall back to showing the raw patch text

    def _simulate_update(original_text: str, upd: UpdateFile) -> str:
        """Apply UpdateFile chunks to original_text and return the new content (no write)."""
        lines = original_text.splitlines()
        replacements: List[Tuple[int, int, List[str]]] = []
        line_index = 0

        for chunk in upd.chunks:
            pattern = chunk.old_lines
            found = seek_sequence(lines, pattern, line_index, chunk.is_end_of_file)

            # Retry without trailing empty line if not found
            if found is None and pattern and pattern[-1] == "":
                pattern = pattern[:-1]
                new_lines = chunk.new_lines
                if new_lines and new_lines[-1] == "":
                    new_lines = new_lines[:-1]
                found = seek_sequence(lines, pattern, line_index, chunk.is_end_of_file)
            else:
                new_lines = chunk.new_lines

            if found is None:
                # If we can't find the pattern, raise to trigger fallback
                context_hint = (
                    f" (context: {', '.join(chunk.context_headers)})"
                    if chunk.context_headers
                    else ""
                )
                raise ValueError(
                    f"Failed to locate patch chunk in {upd.path}{context_hint}"
                )

            replacements.append((found, len(pattern), new_lines))
            line_index = found + len(pattern)

        # Apply replacements in reverse order
        for start_idx, old_len, new_lines in reversed(replacements):
            # Remove old
            for _ in range(old_len):
                if start_idx < len(lines):
                    del lines[start_idx]
            # Insert new
            for offset, new_line in enumerate(new_lines):
                lines.insert(start_idx + offset, new_line)

        new_content = "\n".join(lines)
        if lines and not new_content.endswith("\n"):
            new_content += "\n"
        return new_content

    try:
        hunks = parse_patch(patch)
        file_count = len(hunks)

        # Count operations
        adds = sum(1 for h in hunks if isinstance(h, AddFile))
        deletes = sum(1 for h in hunks if isinstance(h, DeleteFile))
        updates = sum(1 for h in hunks if isinstance(h, UpdateFile))

        summary = f"Apply patch ({adds} adds, {updates} updates, {deletes} deletes)"

        # Build combined diff_lines covering all file operations
        combined_diff: List[str] = []

        for h in hunks:
            if isinstance(h, AddFile):
                # Show diff from /dev/null to new file
                new_lines = h.contents.splitlines(keepends=True)
                file_diff = list(
                    difflib.unified_diff(
                        [],
                        new_lines,
                        fromfile="/dev/null",
                        tofile=h.path,
                        lineterm="",
                        n=3,
                    )
                )
                combined_diff.extend(file_diff)
            elif isinstance(h, DeleteFile):
                try:
                    with open(h.path, "r", encoding="utf-8", errors="replace") as f:
                        old_text = f.read()
                except Exception:
                    # If file doesn't exist or can't be read, show a synthetic header
                    old_text = ""
                old_lines = old_text.splitlines(keepends=True)
                file_diff = list(
                    difflib.unified_diff(
                        old_lines,
                        [],
                        fromfile=h.path,
                        tofile="/dev/null",
                        lineterm="",
                        n=3,
                    )
                )
                combined_diff.extend(file_diff)
            elif isinstance(h, UpdateFile):
                try:
                    with open(h.path, "r", encoding="utf-8", errors="replace") as f:
                        original_text = f.read()
                except Exception:
                    # If we can't read the original file, we can't compute a real diff
                    # Fallback to a pseudo-diff using new lines only
                    original_text = ""

                try:
                    new_text = _simulate_update(original_text, h)
                    fromfile = h.path
                    tofile = h.move_path or h.path
                    old_lines = original_text.splitlines(keepends=True)
                    new_lines = new_text.splitlines(keepends=True)
                    file_diff = list(
                        difflib.unified_diff(
                            old_lines,
                            new_lines,
                            fromfile=fromfile,
                            tofile=tofile,
                            lineterm="",
                            n=3,
                        )
                    )
                    combined_diff.extend(file_diff)
                except Exception:
                    # If simulation fails, add a clear separator and include raw patch fragment
                    combined_diff.append(f"--- {h.path}")
                    combined_diff.append(f"+++ {h.move_path or h.path}")
                    # Add a marker so users know this is a fallback
                    combined_diff.append(
                        f"@@ Unable to compute exact diff for {h.path} @@"
                    )
                    # Include context headers if present to help humans
                    for chunk in h.chunks:
                        for header in chunk.context_headers:
                            combined_diff.append(f"@@ {header}")
                        for old_line in chunk.old_lines:
                            combined_diff.append("-" + old_line)
                        for new_line in chunk.new_lines:
                            combined_diff.append("+" + new_line)

        # If we didn't produce any diff (e.g., empty patch), fall back to raw patch content
        if not combined_diff:
            combined_diff = patch.splitlines()

        # Compute a preview and truncation flag
        preview_lines = combined_diff[:80] if combined_diff else ["No changes"]
        preview = (
            "".join(preview_lines)
            if combined_diff
            and isinstance(combined_diff[0], str)
            and combined_diff[0].endswith("\n")
            else "\n".join(preview_lines)
        )

        # Count added/removed lines for metadata (skip headers)
        additions = sum(
            1
            for l in combined_diff
            if isinstance(l, str) and l.startswith("+") and not l.startswith("+++")
        )
        deletions = sum(
            1
            for l in combined_diff
            if isinstance(l, str) and l.startswith("-") and not l.startswith("---")
        )

        return {
            "display_type": "diff",
            "summary": summary,
            "value": {
                # Bubble Tea diff renderer expects a list of lines without newlines
                "diff_lines": [
                    ln[:-1] if ln.endswith("\n") else ln for ln in combined_diff
                ],
                "diff_text": patch,
            },
            "preview": preview,
            "truncated": len(combined_diff) > 80,
            "metadata": {
                "operation": "apply_patch",
                "file_count": file_count,
                "additions": additions,
                "deletions": deletions,
            },
        }
    except Exception:
        # Fallback: show raw patch text, still as a diff display so the UI reserves space
        lines = patch.splitlines()
        preview = "\n".join(lines[:30]) if lines else "Empty patch"
        return {
            "display_type": "diff",
            "summary": "Apply patch",
            "value": {
                "diff_lines": lines,
                "diff_text": patch,
            },
            "preview": preview,
            "truncated": len(lines) > 30,
            "metadata": {
                "operation": "apply_patch",
                "file_count": 0,
            },
        }


@rich_function_tool(running_summary="Applying patch...")
async def apply_patch(
    ctx: RunContextWrapper[Any],
    patch: str,
) -> RichToolOutput:
    """
    Use the apply_patch tool to edit files. Your patch uses a custom format designed for easy AI generation.

    Args:
        patch: The entire contents of the apply_patch command in the format below

    PATCH FORMAT - You create patches like this:

    *** Begin Patch
    [ one or more file sections ]
    *** End Patch

    Within that envelope, you can perform three types of file operations:

    1. CREATE A NEW FILE:
    *** Add File: <path>
    +<line 1 of content>
    +<line 2 of content>
    +<etc...>

    2. DELETE AN EXISTING FILE:
    *** Delete File: <path>

    3. UPDATE AN EXISTING FILE:
    *** Update File: <path>
    *** Move to: <new_path>  (optional - use this to rename the file)
    @@ <optional context header to help locate the change>
    -<old line to remove>
    -<another old line to remove>
    +<new line to add>
    +<another new line to add>
     <context line that stays the same (space prefix)>
    *** End of File  (optional - use when editing at end of file)

    IMPORTANT RULES:
    - ALWAYS include '*** Begin Patch' and '*** End Patch' wrapper
    - ALWAYS include a header (*** Add File / *** Delete File / *** Update File)
    - For new files, EVERY line must start with '+'
    - For updates, use '-' for lines to remove, '+' for lines to add, ' ' (space) for context
    - File paths should be relative, not absolute (use 'src/main.py' not '/home/user/src/main.py')
    - Context lines help locate where to make changes - include 3 lines before and after changes

    CONTEXT HEADERS (@@ lines):
    Use @@ headers to specify WHERE in the file to make changes:
    - '@@ def function_name():' - make changes in this function
    - '@@ class ClassName:' - make changes in this class
    - '@@ # Section Header' - make changes near this comment
    You can use multiple @@ headers to narrow down the location:
    @@ class MyClass:
    @@ def my_method():

    FUZZY MATCHING:
    The tool uses fuzzy matching to find the code to replace:
    - Exact match is tried first
    - Then ignoring trailing whitespace
    - Then ignoring all leading/trailing whitespace
    - Finally with Unicode normalization (smart quotes→ASCII, em-dash→hyphen, etc.)
    This means minor formatting differences won't break your patches!

    COMPLETE EXAMPLE - Multiple operations in one patch:
    *** Begin Patch
    *** Add File: hello.txt
    +Hello world
    +This is a new file
    *** Update File: src/app.py
    *** Move to: src/main.py
    @@ def greet():
     print("Starting greeting")
    -print("Hi")
    +print("Hello, world!")
     print("Greeting complete")
    *** Delete File: obsolete.txt
    *** End Patch

    TIPS FOR SUCCESS:
    - Include enough context lines to uniquely identify where to make changes
    - Use @@ headers when editing specific functions or classes
    - The tool will try hard to match your patterns even with whitespace differences
    - When creating files, remember the '+' prefix on EVERY line
    - You can combine multiple file operations in a single patch
    """
    from omniagents import workspace as _ws

    try:
        # Parse the patch
        base_dir = _workspace_root_from_ctx(ctx)
        hunks = parse_patch(patch, base_dir)

        if not hunks:
            llm_output = "No changes in patch"
            ui_metadata = {
                "value": "No changes in patch",
                "display_type": "info",
                "summary": "Empty patch",
                "preview": "No changes to apply",
                "truncated": False,
                "metadata": {"operation": "apply_patch", "changes": 0},
            }
            return RichToolOutput(llm_output, ui_metadata)

        # Before applying, compute a combined unified diff just like apply_patch_approval
        # so we can show a helpful diff preview in the tool result as well.
        def _simulate_update(original_text: str, upd: UpdateFile) -> str:
            """Apply UpdateFile chunks to original_text and return the new content (no write)."""
            lines = original_text.splitlines()
            replacements: List[Tuple[int, int, List[str]]] = []
            line_index = 0

            for chunk in upd.chunks:
                pattern = chunk.old_lines
                found = seek_sequence(lines, pattern, line_index, chunk.is_end_of_file)

                # Retry without trailing empty line if not found
                if found is None and pattern and pattern[-1] == "":
                    pattern = pattern[:-1]
                    new_lines = chunk.new_lines
                    if new_lines and new_lines[-1] == "":
                        new_lines = new_lines[:-1]
                    found = seek_sequence(
                        lines, pattern, line_index, chunk.is_end_of_file
                    )
                else:
                    new_lines = chunk.new_lines

                if found is None:
                    context_hint = (
                        f" (context: {', '.join(chunk.context_headers)})"
                        if chunk.context_headers
                        else ""
                    )
                    raise ValueError(
                        f"Failed to locate patch chunk in {upd.path}{context_hint}"
                    )

                replacements.append((found, len(pattern), new_lines))
                line_index = found + len(pattern)

            # Apply replacements in reverse order
            for start_idx, old_len, new_lines in reversed(replacements):
                for _ in range(old_len):
                    if start_idx < len(lines):
                        del lines[start_idx]
                for offset, new_line in enumerate(new_lines):
                    lines.insert(start_idx + offset, new_line)

            new_content = "\n".join(lines)
            if lines and not new_content.endswith("\n"):
                new_content += "\n"
            return new_content

        # Operation counts for summary
        adds = sum(1 for h in hunks if isinstance(h, AddFile))
        deletes = sum(1 for h in hunks if isinstance(h, DeleteFile))
        updates = sum(1 for h in hunks if isinstance(h, UpdateFile))

        combined_diff: List[str] = []

        for h in hunks:
            if isinstance(h, AddFile):
                new_lines = h.contents.splitlines(keepends=True)
                file_diff = list(
                    difflib.unified_diff(
                        [],
                        new_lines,
                        fromfile="/dev/null",
                        tofile=h.path,
                        lineterm="",
                        n=3,
                    )
                )
                combined_diff.extend(file_diff)
            elif isinstance(h, DeleteFile):
                try:
                    old_text = await _ws.read_text(h.path)
                except Exception:
                    old_text = ""
                old_lines = old_text.splitlines(keepends=True)
                file_diff = list(
                    difflib.unified_diff(
                        old_lines,
                        [],
                        fromfile=h.path,
                        tofile="/dev/null",
                        lineterm="",
                        n=3,
                    )
                )
                combined_diff.extend(file_diff)
            elif isinstance(h, UpdateFile):
                try:
                    original_text = await _ws.read_text(h.path)
                except Exception:
                    original_text = ""

                try:
                    new_text = _simulate_update(original_text, h)
                    fromfile = h.path
                    tofile = h.move_path or h.path
                    old_lines = original_text.splitlines(keepends=True)
                    new_lines = new_text.splitlines(keepends=True)
                    file_diff = list(
                        difflib.unified_diff(
                            old_lines,
                            new_lines,
                            fromfile=fromfile,
                            tofile=tofile,
                            lineterm="",
                            n=3,
                        )
                    )
                    combined_diff.extend(file_diff)
                except Exception:
                    combined_diff.append(f"--- {h.path}")
                    combined_diff.append(f"+++ {h.move_path or h.path}")
                    combined_diff.append(
                        f"@@ Unable to compute exact diff for {h.path} @@"
                    )
                    for chunk in h.chunks:
                        for header in chunk.context_headers:
                            combined_diff.append(f"@@ {header}")
                        for old_line in chunk.old_lines:
                            combined_diff.append("-" + old_line)
                        for new_line in chunk.new_lines:
                            combined_diff.append("+" + new_line)

        if not combined_diff:
            combined_diff = patch.splitlines()

        preview_lines = combined_diff[:80] if combined_diff else ["No changes"]
        preview = (
            "".join(preview_lines)
            if combined_diff
            and isinstance(combined_diff[0], str)
            and combined_diff[0].endswith("\n")
            else "\n".join(preview_lines)
        )

        additions = sum(
            1
            for l in combined_diff
            if isinstance(l, str) and l.startswith("+") and not l.startswith("+++")
        )
        deletions = sum(
            1
            for l in combined_diff
            if isinstance(l, str) and l.startswith("-") and not l.startswith("---")
        )

        # Now apply the hunks to actually write the changes
        results = await apply_hunks(hunks)

        # Build LLM-facing summary
        summary_lines = []
        if results["added"]:
            summary_lines.append(f"Added {len(results['added'])} files:")
            for path in results["added"]:
                summary_lines.append(f"  A {path}")
        if results["modified"]:
            summary_lines.append(f"Modified {len(results['modified'])} files:")
            for path in results["modified"]:
                summary_lines.append(f"  M {path}")
        if results["deleted"]:
            summary_lines.append(f"Deleted {len(results['deleted'])} files:")
            for path in results["deleted"]:
                summary_lines.append(f"  D {path}")

        llm_output = "Patch applied successfully.\n" + "\n".join(summary_lines)

        total_changes = (
            len(results["added"]) + len(results["modified"]) + len(results["deleted"])
        )
        ui_metadata = {
            "display_type": "diff",
            "summary": f"Applied patch ({adds} adds, {updates} updates, {deletes} deletes) — {total_changes} file(s) changed",
            "value": {
                # Bubble Tea diff renderer expects lines without trailing newlines
                "diff_lines": [
                    ln[:-1] if isinstance(ln, str) and ln.endswith("\n") else ln
                    for ln in combined_diff
                ],
                "diff_text": patch,
            },
            "preview": preview,
            "truncated": len(combined_diff) > 80,
            "metadata": {
                "operation": "apply_patch",
                "file_count": len(hunks),
                "additions": additions,
                "deletions": deletions,
                "added_files": results.get("added", []),
                "modified_files": results.get("modified", []),
                "deleted_files": results.get("deleted", []),
                "added_count": len(results.get("added", [])),
                "modified_count": len(results.get("modified", [])),
                "deleted_count": len(results.get("deleted", [])),
                "total_changes": total_changes,
            },
        }

        return RichToolOutput(llm_output, ui_metadata)

    except Exception as e:
        llm_output = f"Error applying patch: {str(e)}"

        ui_metadata = {
            "value": f"Error applying patch: {str(e)}",
            "display_type": "error",
            "summary": "Patch application failed",
            "preview": str(e),
            "truncated": False,
            "metadata": {"error_type": "patch_error", "error_message": str(e)},
        }

        return RichToolOutput(llm_output, ui_metadata)
