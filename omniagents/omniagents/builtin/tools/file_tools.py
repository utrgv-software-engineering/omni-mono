import os
import glob
import re
import fnmatch
import difflib
from typing import List, Optional, Dict, Tuple, Union, Any
from omniagents.core.tools import rich_function_tool, RichToolOutput, approval_metadata
from functools import lru_cache
import pygit2
from agents.run_context import RunContextWrapper

# ----------------------------
# Hidden/Git ignore utilities
# ----------------------------

# --- libgit2-backed ignore (no fallbacks) ---

@lru_cache(maxsize=16)
def _get_pygit2_repo(start_dir: str = "."):
    """Discover and return a pygit2.Repository for start_dir.

    Returns None if start_dir is not inside a Git repository.
    """
    abspath = os.path.abspath(start_dir)
    gitdir = pygit2.discover_repository(abspath)
    if not gitdir:
        return None
    return pygit2.Repository(gitdir)

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
        if os.path.isdir(abspath) and not check_path.endswith('/'):
            check_path = rel + '/'
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

@lru_cache(maxsize=65536)
def _has_hidden_segment(path: str) -> bool:
    """Return True if any path segment is a hidden name (starts with a dot).

    This treats files or any ancestor directory starting with '.' as hidden.
    """
    try:
        norm = os.path.normpath(path)
        # Split into components and check any that start with '.'
        for part in norm.split(os.sep):
            if part.startswith('.') and part not in ('.', '..'):
                return True
        return False
    except Exception:
        return False

def _is_binary_file(path: str) -> bool:
    try:
        if not os.path.isfile(path):
            return False
        with open(path, 'rb') as f:
            chunk = f.read(8192)
        if not chunk:
            return False
        if b'\0' in chunk:
            return True
        chunk.decode('utf-8')
        return False
    except UnicodeDecodeError:
        return True
    except Exception:
        return False

def _should_skip(
    candidate_path: str,
    repo_root: str,
    respect_gitignore: bool,
    ignore_hidden: bool,
) -> bool:
    """Return True if candidate should be excluded based on flags."""
    if respect_gitignore and _is_ignored(candidate_path, repo_root):
        return True
    if ignore_hidden and _has_hidden_segment(candidate_path):
        return True
    return False

def _workspace_root_from_ctx(ctx: RunContextWrapper[Any] | None) -> Optional[str]:
    try:
        if ctx is None:
            return None
        c = getattr(ctx, 'context', None)
        if c is None:
            return None
        if isinstance(c, dict):
            wr = c.get('workspace_root')
        else:
            wr = getattr(c, 'workspace_root', None)
        if isinstance(wr, str) and wr.strip():
            return os.path.abspath(wr)
    except Exception:
        pass
    return None

def _resolve_path(ctx: RunContextWrapper[Any] | None, path: Optional[str]) -> str:
    wr = _workspace_root_from_ctx(ctx)
    if path is None or (isinstance(path, str) and path.strip() == ""):
        return wr or "."
    p = path if isinstance(path, str) else str(path)
    if os.path.isabs(p):
        return p
    if wr:
        return os.path.abspath(os.path.join(wr, p))
    return p

@rich_function_tool
def glob_files(
    ctx: RunContextWrapper[Any],
    pattern: str,
    path: Optional[str] = None,
    ignore_hidden: Optional[bool] = None,
    respect_gitignore: Optional[bool] = None,
) -> RichToolOutput:
    """
    Finds files based on pattern matching using glob patterns.
    
    Args:
        pattern: The glob pattern to match files (e.g., "*.py", "**/*.json")
        path: Directory or file to search in. Defaults to the workspace root when omitted.
        ignore_hidden: If True, exclude files/dirs with any hidden segment (names starting with '.')
        respect_gitignore: If True, exclude paths ignored by Git (.gitignore, .git/info/exclude, etc.)
        
    Returns:
        Tuple of (llm_output, ui_metadata) where:
        - llm_output: Text description of files found
        - ui_metadata: Rich metadata for UI rendering
        Binary files are skipped.
        
    Usage:
        Use this tool to find files matching specific patterns.
        Common patterns:
        - "*.py": All Python files in the current directory
        - "**/*.json": All JSON files in the current directory and subdirectories
        - "src/*.js": All JavaScript files in the src directory
        - If path is not provided, current directory "./" will be used
        - The path argument can point to a single file or a directory
        
    Examples:
        # Find all Python files in the current directory
        glob_files("*.py", path="./")
        
        # Find all JSON files recursively
        glob_files("**/*.json", path="./")
        
        # Include hidden files and gitignored files
        glob_files("**/*", path="./", ignore_hidden=False, respect_gitignore=False)
        
        # Find all log files in a specific directory
        glob_files("*.log", path="/var/log")
    """
    try:
        # Defaults
        resolved_path = _resolve_path(ctx, path)
        resolved_path = os.path.abspath(resolved_path)
        base_is_file = os.path.isfile(resolved_path)
        relative_base = os.path.dirname(resolved_path) if base_is_file else resolved_path
        if not relative_base:
            relative_base = "."
        relative_base = os.path.abspath(relative_base)
        repo_root = relative_base
        if ignore_hidden is None:
            ignore_hidden = False  # preserve current behavior
        if respect_gitignore is None:
            respect_gitignore = True  # preserve current behavior
            
        # General implementation that honors '**' semantics and includes hidden items
        # when ignore_hidden is False, regardless of Python's glob quirks.

        def split_segments(p: str) -> List[str]:
            parts = []
            for seg in p.replace('\\', '/').split('/'):
                if seg and seg != '.':
                    parts.append(seg)
            return parts

        pat_segs = split_segments(pattern or "*")

        def match_segments(pat: List[str], segs: List[str]) -> bool:
            i = j = 0
            while i < len(pat) and j < len(segs):
                if pat[i] == '**':
                    if i == len(pat) - 1:
                        return True
                    i_next = i + 1
                    while j <= len(segs):
                        if match_segments(pat[i_next:], segs[j:]):
                            return True
                        j += 1
                    return False
                else:
                    if not fnmatch.fnmatch(segs[j], pat[i]):
                        return False
                    i += 1
                    j += 1
            while i < len(pat) and pat[i] == '**':
                i += 1
            return i == len(pat) and j == len(segs)

        def rel_segments(base_dir: str, candidate: str) -> List[str]:
            rel = os.path.relpath(candidate, base_dir)
            return split_segments(rel)

        matches: List[str] = []

        skipped_binary = 0
        if base_is_file:
            if not _should_skip(resolved_path, repo_root, respect_gitignore, ignore_hidden):
                if _is_binary_file(resolved_path):
                    skipped_binary += 1
                else:
                    segs = rel_segments(repo_root, resolved_path)
                    if match_segments(pat_segs, segs):
                        matches.append(resolved_path)
        else:
            for root, dirs, filenames in os.walk(resolved_path):
                pruned = []
                for d in list(dirs):
                    d_full = os.path.join(root, d)
                    if _should_skip(d_full, repo_root, respect_gitignore, ignore_hidden):
                        pruned.append(d)
                for d in pruned:
                    if d in dirs:
                        dirs.remove(d)

                for d in dirs:
                    d_full = os.path.join(root, d)
                    segs = rel_segments(relative_base, d_full)
                    if match_segments(pat_segs, segs):
                        matches.append(d_full)

                for fname in filenames:
                    f_full = os.path.join(root, fname)
                    if _should_skip(f_full, repo_root, respect_gitignore, ignore_hidden):
                        continue
                    if _is_binary_file(f_full):
                        skipped_binary += 1
                        continue
                    segs = rel_segments(relative_base, f_full)
                    if match_segments(pat_segs, segs):
                        matches.append(f_full)

        # Sort matches by modification time (newest first)
        matches.sort(key=lambda x: os.path.getmtime(x) if os.path.exists(x) else 0, reverse=True)
        
        # Create preview of matches
        preview_limit = 20
        if len(matches) > preview_limit:
            preview_matches = matches[:preview_limit]
            truncated = True
        else:
            preview_matches = matches
            truncated = False
        
        # Create LLM output
        if len(matches) == 0:
            llm_output = f"No files found matching pattern '{pattern}' in {resolved_path}"
            if skipped_binary:
                llm_output += f" (skipped {skipped_binary} binary file(s))"
        elif len(matches) == 1:
            llm_output = f"Found 1 file matching '{pattern}': {matches[0]}"
        else:
            llm_output = f"Found {len(matches)} files matching '{pattern}':\n" + "\n".join(matches[:10])
            if len(matches) > 10:
                llm_output += f"\n... and {len(matches) - 10} more"
        
        # Create UI metadata
        ui_metadata = {
            "value": matches,  # The actual result - list of matching files
            "display_type": "search_results",
            "summary": f"Found {len(matches)} files matching '{pattern}'",
            "preview": "\n".join(preview_matches),
            "truncated": truncated,
            "metadata": {
                "query": pattern,
                "result_count": len(matches),
                "match_count": len(matches),
                "search_type": "glob",
                "scope": path if path not in (None, "") else resolved_path,
                "preview_count": len(preview_matches),
                "ignore_hidden": bool(ignore_hidden),
                "respect_gitignore": bool(respect_gitignore),
                "skipped_binary": skipped_binary,
            }
        }

        return RichToolOutput(llm_output, ui_metadata)
    except Exception as e:
        llm_output = f"Error finding files: {str(e)}"
        
        ui_metadata = {
            "value": f"Error finding files: {str(e)}",
            "display_type": "error",
            "summary": "File search failed",
            "preview": str(e),
            "truncated": False,
            "metadata": {
                "error_type": "glob_error",
                "pattern": pattern,
                "path": path,
                "ignore_hidden": bool(ignore_hidden) if ignore_hidden is not None else None,
                "respect_gitignore": bool(respect_gitignore) if respect_gitignore is not None else None,
            }
        }
        
        return RichToolOutput(llm_output, ui_metadata)

@rich_function_tool
def grep_files(
    ctx: RunContextWrapper[Any],
    pattern: str,
    path: Optional[str] = None,
    include: Optional[str] = None,
    ignore_hidden: Optional[bool] = None,
    respect_gitignore: Optional[bool] = None,
) -> RichToolOutput:
    """
    Searches for patterns in file contents using regular expressions.
    
    Args:
        pattern: The regular expression pattern to search for
        path: Directory or file to search in. Defaults to the workspace root when omitted.
        include: Optional file pattern (glob) to filter which files to search (e.g., "*.py")
        ignore_hidden: If True, exclude files/dirs with any hidden segment (names starting with '.')
        respect_gitignore: If True, exclude paths ignored by Git (.gitignore, .git/info/exclude, etc.)
        
    Returns:
        Tuple of (llm_output, ui_metadata) where:
        - llm_output: Text description of matches found
        - ui_metadata: Rich metadata for UI rendering
        Binary files are skipped.
        
    Usage:
        Use this tool to search file contents for specific patterns.
        - If path is not provided, the workspace root (or current directory) will be used
        - If include is not provided, all files will be searched
        - The path argument can point to a single file or a directory
        
    Examples:
        # Find all occurrences of "TODO" in Python files
        grep_files("TODO", path="./", include="*.py")
        
        # Search for function definitions in the src directory
        grep_files("def\\s+\\w+\\(", path="src", include="*.py")
        
        # Find all imports in Python files
        grep_files("^import\\s+\\w+", path="./", include="*.py")
        
        # Search all tracked files, including hidden ones
        grep_files("TODO", path="./", include=None, ignore_hidden=False, respect_gitignore=True)
    """
    try:
        # Defaults
        resolved_path = _resolve_path(ctx, path)
        resolved_path = os.path.abspath(resolved_path)
        base_is_file = os.path.isfile(resolved_path)
        repo_root = os.path.dirname(resolved_path) if base_is_file else resolved_path
        if not repo_root:
            repo_root = "."
        repo_root = os.path.abspath(repo_root)
        if ignore_hidden is None:
            ignore_hidden = False
        if respect_gitignore is None:
            respect_gitignore = True

        results = []
        total_matches = 0
        skipped_gitignored = 0
        skipped_hidden = 0
        skipped_binary = 0

        # Compile the regex pattern
        regex = re.compile(pattern)

        # Walk the tree and collect candidate files, pruning ignored/hidden dirs early
        files_to_search: List[str] = []
        real_to_index: Dict[str, int] = {}

        def record_file(candidate: str) -> None:
            nonlocal skipped_gitignored, skipped_hidden, skipped_binary
            if respect_gitignore and _is_ignored(candidate, repo_root):
                skipped_gitignored += 1
                return
            if ignore_hidden and _has_hidden_segment(candidate):
                skipped_hidden += 1
                return
            if os.path.isfile(candidate) and _is_binary_file(candidate):
                skipped_binary += 1
                return
            real_path = os.path.realpath(candidate)
            existing_idx = real_to_index.get(real_path)
            if existing_idx is not None:
                if os.path.islink(candidate):
                    return
                existing_path = files_to_search[existing_idx]
                if os.path.islink(existing_path):
                    files_to_search[existing_idx] = candidate
                return
            real_to_index[real_path] = len(files_to_search)
            files_to_search.append(candidate)

        if base_is_file:
            record_file(resolved_path)
        else:
            for root, dirs, filenames in os.walk(resolved_path):
                pruned_dirs = []
                for d in list(dirs):
                    d_full = os.path.join(root, d)
                    is_ignored = bool(respect_gitignore and _is_ignored(d_full, repo_root))
                    is_hidden = bool(ignore_hidden and _has_hidden_segment(d_full))
                    if is_ignored or is_hidden:
                        pruned_dirs.append(d)
                        if is_ignored:
                            skipped_gitignored += 1
                        elif is_hidden:
                            skipped_hidden += 1
                for d in pruned_dirs:
                    if d in dirs:
                        dirs.remove(d)

                for fname in filenames:
                    f_full = os.path.join(root, fname)
                    record_file(f_full)

        # Apply include as a recursive filter like grep's --include
        if include is not None and include != "":
            filtered: List[str] = []
            for f in files_to_search:
                rel = os.path.relpath(f, repo_root)
                if fnmatch.fnmatch(rel, include) or fnmatch.fnmatch(os.path.basename(f), include):
                    filtered.append(f)
            files_to_search = filtered
        
        # Sort files by modification time (newest first)
        files_to_search.sort(key=lambda x: os.path.getmtime(x) if os.path.exists(x) else 0, reverse=True)
        
        # Search through files
        for file_path in files_to_search:
            try:
                with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                    file_matches = []
                    for i, line in enumerate(f, 1):
                        if regex.search(line):
                            file_matches.append({"line_number": i, "content": line.rstrip()})
                    
                    if file_matches:
                        results.append({
                            "file": file_path,
                            "matches": file_matches,
                            "match_count": len(file_matches)
                        })
                        total_matches += len(file_matches)
            except Exception as e:
                # Skip files that can't be read
                continue
        
        # Create preview showing first few matches
        preview_lines = []
        preview_limit = 5  # Show first 5 file results
        for i, file_result in enumerate(results[:preview_limit]):
            file_path = file_result['file']
            match_count = file_result['match_count']
            preview_lines.append(f"{file_path}: {match_count} matches")
            # Show first match from this file
            if file_result['matches']:
                first_match = file_result['matches'][0]
                preview_lines.append(f"  Line {first_match['line_number']}: {first_match['content'].strip()[:60]}")
        
        preview = "\n".join(preview_lines) if preview_lines else "No matches found"
        truncated = len(results) > preview_limit
        
        # Create LLM output
        if total_matches == 0:
            llm_output = f"No matches found for pattern '{pattern}' in {resolved_path}"
            hints = []
            if skipped_gitignored:
                hints.append(f"skipped {skipped_gitignored} file(s) due to .gitignore")
            if skipped_hidden and ignore_hidden:
                hints.append(f"skipped {skipped_hidden} hidden file(s)")
            if skipped_binary:
                hints.append(f"skipped {skipped_binary} binary file(s)")
            if hints:
                llm_output += " (" + ", ".join(hints) + ")"
        elif len(results) == 1:
            llm_output = f"Found {total_matches} matches for '{pattern}' in 1 file:\n"
            for match in results[0]['matches'][:5]:
                llm_output += f"  {results[0]['file']}:{match['line_number']}: {match['content'].strip()}\n"
            if results[0]['match_count'] > 5:
                llm_output += f"  ... and {results[0]['match_count'] - 5} more matches\n"
        else:
            llm_output = f"Found {total_matches} matches for '{pattern}' in {len(results)} files:\n"
            for i, file_result in enumerate(results[:5]):
                llm_output += f"  {file_result['file']}: {file_result['match_count']} matches\n"
                if file_result['matches'] and i < 3:  # Show first match for first 3 files
                    first_match = file_result['matches'][0]
                    llm_output += f"    Line {first_match['line_number']}: {first_match['content'].strip()[:80]}\n"
            if len(results) > 5:
                llm_output += f"  ... and {len(results) - 5} more files\n"
        
        # Create UI metadata
        ui_metadata = {
            "value": results,  # The actual result - list of match results
            "display_type": "search_results",
            "summary": f"Found {total_matches} matches for '{pattern}' in {len(results)} files",
            "preview": preview,
            "truncated": truncated,
            "metadata": {
                "query": pattern,
                "result_count": total_matches,
                "match_count": total_matches,
                "file_count": len(results),
                "search_type": "grep",
                "scope": f"{include or '*'} in {path if path not in (None, '') else resolved_path}",
                "preview_files": min(len(results), preview_limit),
                "ignore_hidden": bool(ignore_hidden),
                "respect_gitignore": bool(respect_gitignore),
                "skipped_gitignored": skipped_gitignored,
                "skipped_hidden": skipped_hidden,
                "skipped_binary": skipped_binary,
            }
        }
        
        return RichToolOutput(llm_output, ui_metadata)
    except Exception as e:
        llm_output = f"Error searching files: {str(e)}"
        
        ui_metadata = {
            "value": f"Error searching files: {str(e)}",
            "display_type": "error",
            "summary": "Pattern search failed",
            "preview": str(e),
            "truncated": False,
            "metadata": {
                "error_type": "grep_error",
                "pattern": pattern,
                "ignore_hidden": bool(ignore_hidden) if ignore_hidden is not None else None,
                "respect_gitignore": bool(respect_gitignore) if respect_gitignore is not None else None,
            }
        }
        
        return RichToolOutput(llm_output, ui_metadata)

@rich_function_tool
def list_directory(
    ctx: RunContextWrapper[Any],
    path: str,
    recursive: Optional[bool],
    ignore_hidden: Optional[bool] = None,
    respect_gitignore: Optional[bool] = None,
) -> RichToolOutput:
    """
    Lists files and directories in the specified path.
    
    Args:
        path: The directory path to list contents of
        recursive: Whether to list contents recursively
        ignore_hidden: If True, exclude files/dirs with names starting with '.' (and their contents)
        respect_gitignore: If True, exclude paths ignored by Git (.gitignore, .git/info/exclude, etc.)
        
    Returns:
        Tuple of (llm_output, ui_metadata) where:
        - llm_output: Text description of directory contents
        - ui_metadata: Rich metadata for UI rendering
        
    Usage:
        Use this tool to list directory contents.
        - If recursive is not provided, it defaults to False
        - If path is empty ("" or whitespace), the current working directory "./" will be used
        
    Examples:
        # List contents of the current directory
        list_directory(".", recursive=False)
        
        # List contents of a specific directory recursively
        list_directory("/path/to/directory", recursive=True)
        
        # Show everything, including hidden and gitignored
        list_directory(".", recursive=True, ignore_hidden=False, respect_gitignore=False)
    """
    try:
        # Defaults
        path = _resolve_path(ctx, path)
        if recursive is None:
            recursive = False
        if ignore_hidden is None:
            ignore_hidden = False  # Preserve current default behavior
        if respect_gitignore is None:
            respect_gitignore = True  # Preserve current default behavior
            
        files = []
        directories = []
        
        if recursive:
            for root, dirs, filenames in os.walk(path):
                # Remove ignored/hidden directories so os.walk does NOT recurse into
                # them, preventing huge listings (e.g., .venv)
                dirs[:] = [
                    d for d in dirs
                    if not _should_skip(os.path.join(root, d), path, respect_gitignore, ignore_hidden)
                ]
                
                # Add directories that are not ignored
                for dir_name in dirs:
                    dir_path = os.path.join(root, dir_name)
                    directories.append(dir_path)
                
                # Add files
                for filename in filenames:
                    file_path = os.path.join(root, filename)
                    if not _should_skip(file_path, path, respect_gitignore, ignore_hidden):
                        files.append(file_path)
        else:
            # List only the specified directory
            with os.scandir(path) as entries:
                for entry in entries:
                    if entry.is_file():
                        if not _should_skip(entry.path, path, respect_gitignore, ignore_hidden):
                            files.append(entry.path)
                    elif entry.is_dir():
                        if not _should_skip(entry.path, path, respect_gitignore, ignore_hidden):
                            directories.append(entry.path)
        
        # Sort by name
        files.sort()
        directories.sort()
        
        # Create preview
        total_items = len(files) + len(directories)
        preview_lines = []
        preview_limit = 20
        
        # Add directories first (with trailing /)
        for dir_path in directories[:min(10, preview_limit//2)]:
            rel_path = os.path.relpath(dir_path, path) if recursive else os.path.basename(dir_path)
            preview_lines.append(f"📁 {rel_path}/")
        
        # Then add files
        remaining = preview_limit - len(preview_lines)
        for file_path in files[:remaining]:
            rel_path = os.path.relpath(file_path, path) if recursive else os.path.basename(file_path)
            preview_lines.append(f"📄 {rel_path}")
        
        preview = "\n".join(preview_lines) if preview_lines else "Empty directory"
        truncated = total_items > preview_limit
        
        # Create LLM output
        if total_items == 0:
            llm_output = f"Directory {path} is empty"
        else:
            llm_output = f"Listed {total_items} items in {path}:\n"
            llm_output += f"  {len(directories)} directories\n"
            llm_output += f"  {len(files)} files\n"
            if preview_lines:
                llm_output += "\nContents:\n" + "\n".join(preview_lines[:10])
                if total_items > 10:
                    llm_output += f"\n... and {total_items - 10} more items"
        
        # Create UI metadata
        ui_metadata = {
            "value": {"files": files, "directories": directories},  # The actual result
            "display_type": "directory_listing",
            "summary": f"Listed {total_items} items in {path}",
            "preview": preview,
            "truncated": truncated,
            "metadata": {
                "path": path,
                "file_count": len(files),
                "dir_count": len(directories),
                "total_count": total_items,
                "recursive": recursive,
                "preview_count": min(total_items, preview_limit),
                "ignore_hidden": bool(ignore_hidden),
                "respect_gitignore": bool(respect_gitignore),
            }
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
                "ignore_hidden": bool(ignore_hidden) if ignore_hidden is not None else None,
                "respect_gitignore": bool(respect_gitignore) if respect_gitignore is not None else None,
            }
        }
        
        return RichToolOutput(llm_output, ui_metadata)

@rich_function_tool
def read_file(ctx: RunContextWrapper[Any], file_path: str, start_line: Optional[int], num_lines: Optional[int]) -> RichToolOutput:
    """
    Reads the contents of a file.
    
    Args:
        file_path: Path to the file to read
        start_line: 1-based line number to start reading from
        num_lines: Maximum number of lines to read
        
    Returns:
        Tuple of (llm_output, ui_metadata) where:
        - llm_output: The file content as a string
        - ui_metadata: Rich metadata for UI rendering
        
    Usage:
        Use this tool to read the contents of files.
        - If start_line is not provided, it defaults to 1 (first line)
        - If num_lines is not provided, all remaining lines will be read
        
    Examples:
        # Read an entire file
        read_file("/path/to/file.txt", start_line=1, num_lines=null)
        
        # Read 10 lines starting from line 5
        read_file("/path/to/file.txt", start_line=5, num_lines=10)
    """
    try:
        # Default to start at beginning if not specified (1-based)
        if start_line is None:
            start_line = 1
            
        file_path = _resolve_path(ctx, file_path)
        with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
            # Read all lines
            all_lines = f.readlines()
            
            # Apply start_line and num_lines filters (1-based to 0-based)
            if start_line < 1:
                start_line = 1
            
            start_idx = start_line - 1
            
            if num_lines is None:
                # Read all remaining lines
                selected_lines = all_lines[start_idx:]
            else:
                # Read specified number of lines
                selected_lines = all_lines[start_idx:start_idx + max(0, num_lines)]
            
            content = "".join(selected_lines)
            
            # Detect file language from extension
            ext = os.path.splitext(file_path)[1].lower()
            language_map = {
                '.py': 'python', '.js': 'javascript', '.ts': 'typescript',
                '.java': 'java', '.cpp': 'cpp', '.c': 'c', '.cs': 'csharp',
                '.go': 'go', '.rs': 'rust', '.rb': 'ruby', '.php': 'php',
                '.swift': 'swift', '.kt': 'kotlin', '.scala': 'scala',
                '.html': 'html', '.css': 'css', '.json': 'json', '.xml': 'xml',
                '.yaml': 'yaml', '.yml': 'yaml', '.toml': 'toml', '.md': 'markdown'
            }
            language = language_map.get(ext, 'text')
            
            # Create preview
            preview_limit = 20
            if len(selected_lines) > preview_limit:
                preview_lines = selected_lines[:preview_limit]
                preview = "".join(preview_lines)
                truncated = True
            else:
                preview = content
                truncated = False
            
            # LLM output is just the content
            llm_output = content
            
            # Create UI metadata
            ui_metadata = {
                "value": content,  # The actual file content
                "display_type": "file_content",
                "summary": f"Read {len(selected_lines)} lines from {os.path.basename(file_path)}",
                "preview": preview,
                "truncated": truncated,
                "metadata": {
                    "file_path": file_path,
                    "line_count": len(selected_lines),
                    "file_size": len(content),
                    "language": language,
                    "start_line": start_line,
                    "preview_lines": min(len(selected_lines), preview_limit)
                }
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
            "metadata": {
                "error_type": "read_error",
                "file_path": file_path
            }
        }
        
        return RichToolOutput(llm_output, ui_metadata)

@rich_function_tool
def edit_file(
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
    try:
        # Read the file content
        file_path = _resolve_path(ctx, file_path)
        with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
        
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
            new_content, replacements = re.subn(pattern, new_text, content, count=count, flags=re.DOTALL | re.MULTILINE)
        else:
            prefix = ''.join(lines[:s_idx])
            target = ''.join(lines[s_idx:e_idx])
            suffix = ''.join(lines[e_idx:])
            replaced, replacements = re.subn(pattern, new_text, target, count=count, flags=re.DOTALL | re.MULTILINE)
            new_content = prefix + replaced + suffix
        
        # Guard: expected replacements
        if expected_replacements is not None and replacements != expected_replacements:
            # Diff for visibility even on failure
            diff_lines = list(difflib.unified_diff(
                content.splitlines(keepends=True),
                new_content.splitlines(keepends=True),
                fromfile=file_path,
                tofile=file_path,
                lineterm='',
                n=3,
            ))
            preview = ''.join(diff_lines[:40]) if diff_lines else "No changes"
            llm_output = (
                f"Edit aborted: expected {expected_replacements} replacement(s), got {replacements}."
            )
            ui_metadata = {
                "display_type": "diff",
                "summary": f"Edit aborted for {os.path.basename(file_path)}",
                "value": {
                    "diff_lines": diff_lines,
                    "diff_text": ''.join(diff_lines),
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
        diff_lines = list(difflib.unified_diff(
            content.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=file_path,
            tofile=file_path,
            lineterm='',
            n=3,
        ))
        preview = ''.join(diff_lines[:80]) if diff_lines else "No changes"
        additions = sum(1 for l in diff_lines if l.startswith('+') and not l.startswith('+++'))
        deletions = sum(1 for l in diff_lines if l.startswith('-') and not l.startswith('---'))
        
        if dry_run:
            llm_output = (
                f"Dry run: would update {os.path.basename(file_path)} - {replacements} replacement(s)"
            )
            ui_metadata = {
                "display_type": "diff",
                "summary": f"Dry run edit for {os.path.basename(file_path)} ({replacements} replacements)",
                "value": {
                    "diff_lines": diff_lines,
                    "diff_text": ''.join(diff_lines),
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
        
        # Write the modified content back to the file
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        
        # LLM output
        llm_output = f"Successfully updated {os.path.basename(file_path)} - made {replacements} replacement(s)"
        
        # UI metadata
        ui_metadata = {
            "display_type": "diff",
            "summary": f"Updated {os.path.basename(file_path)} ({replacements} replacements)",
            "value": {
                "diff_lines": diff_lines,
                "diff_text": ''.join(diff_lines),
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
            "metadata": {
                "error_type": "edit_error",
                "file_path": file_path
            }
        }
        
        return RichToolOutput(llm_output, ui_metadata)

@rich_function_tool
def write_file(ctx: RunContextWrapper[Any], file_path: str, content: str, append: Optional[bool]) -> RichToolOutput:
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
    try:
        # Default to overwrite if not specified
        if append is None:
            append = False
            
        # Resolve file path relative to workspace and ensure directory exists
        file_path = _resolve_path(ctx, file_path)
        os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)
        
        # Write mode: 'a' for append, 'w' for overwrite
        mode = 'a' if append else 'w'
        operation = 'append' if append else 'write'
        
        with open(file_path, mode, encoding='utf-8') as f:
            bytes_written = f.write(content)
        
        # Create preview of written content
        lines = content.split('\n')
        preview_limit = 10
        if len(lines) > preview_limit:
            preview = '\n'.join(lines[:preview_limit])
            truncated = True
        else:
            preview = content[:500]  # Limit preview to 500 chars
            truncated = len(content) > 500
        
        # LLM output
        llm_output = f"Successfully {operation} {os.path.basename(file_path)} ({bytes_written} bytes written)"
        
        # UI metadata
        ui_metadata = {
            "value": f"{bytes_written} bytes written",  # The actual result
            "display_type": "file_edit",
            "summary": f"{operation.capitalize()} {os.path.basename(file_path)} ({bytes_written} bytes)",
            "preview": preview,
            "truncated": truncated,
            "metadata": {
                "file_path": file_path,
                "operation": operation,
                "bytes_written": bytes_written,
                "line_count": len(lines)
            }
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
            "metadata": {
                "error_type": "write_error",
                "file_path": file_path
            }
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
        with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
        lines = content.splitlines(keepends=True)
        total = len(lines)
        s_idx = 0 if start_line is None else max(0, min(total, start_line - 1))
        e_idx = total if end_line is None else max(0, min(total, end_line))
        pattern = old_text if regex else re.escape(old_text)
        count = 0 if max_replacements is None else max_replacements
        if s_idx == 0 and e_idx == total:
            new_content, repl = re.subn(pattern, new_text, content, count=count, flags=re.DOTALL | re.MULTILINE)
        else:
            prefix = ''.join(lines[:s_idx])
            target = ''.join(lines[s_idx:e_idx])
            suffix = ''.join(lines[e_idx:])
            replaced, repl = re.subn(pattern, new_text, target, count=count, flags=re.DOTALL | re.MULTILINE)
            new_content = prefix + replaced + suffix
        diff_lines = list(difflib.unified_diff(
            content.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=file_path,
            tofile=file_path,
            lineterm='',
            n=3,
        ))
    except Exception:
        # Fallback: diff between old_text and new_text parameters
        diff_lines = list(difflib.unified_diff(
            old_text.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile=file_path,
            tofile=file_path,
            lineterm='',
            n=3
        ))
    
    preview_lines = diff_lines[:20] if diff_lines else ["No changes"]
    preview = ''.join(preview_lines)
    additions = sum(1 for l in diff_lines if l.startswith('+') and not l.startswith('+++'))
    deletions = sum(1 for l in diff_lines if l.startswith('-') and not l.startswith('---'))
    
    return {
        "display_type": "diff",  # Tell UI to use diff renderer
        "summary": f"Edit {os.path.basename(file_path) if file_path else 'file'}",
        "value": {
            "old_text": old_text,
            "new_text": new_text,
            "diff_lines": diff_lines
        },
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
        }
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
            with open(file_path, 'r', encoding='utf-8') as f:
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
        diff_lines = list(difflib.unified_diff(
            old_content.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=file_path,
            tofile=file_path,
            lineterm='',
            n=3
        ))
        
        # Create preview from diff
        preview_lines = diff_lines[:20] if diff_lines else ["No changes"]
        preview = ''.join(preview_lines)
        
        # Count additions and deletions
        additions = sum(1 for l in diff_lines if l.startswith('+') and not l.startswith('+++'))
        deletions = sum(1 for l in diff_lines if l.startswith('-') and not l.startswith('---'))
        
        return {
            "display_type": "diff",  # Use diff display for existing files
            "summary": f"{'Append to' if append else 'Overwrite'} {os.path.basename(file_path)}",
            "value": {
                "old_text": old_content[:1000],  # Truncate for memory
                "new_text": new_content[:1000],
                "diff_lines": diff_lines
            },
            "preview": preview,
            "truncated": len(diff_lines) > 20,
            "metadata": {
                "file_path": file_path,
                "operation": "append" if append else "overwrite",
                "additions": additions,
                "deletions": deletions,
                "total_lines": len(diff_lines),
                "file_exists": True
            }
        }
    else:
        # New file creation - show preview without diff
        lines = content.splitlines()
        preview_lines = lines[:10]
        preview = '\n'.join(preview_lines)
        
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
                "file_exists": False
            }
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
    def __init__(self, path: str, chunks: List['UpdateFileChunk'], move_path: Optional[str] = None):
        super().__init__(path)
        self.move_path = move_path
        self.chunks = chunks

class UpdateFileChunk:
    """Represents a chunk of changes within an UpdateFile hunk"""
    def __init__(self, old_lines: List[str], new_lines: List[str], context_headers: List[str] = None, is_end_of_file: bool = False):
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
    lines = patch_text.split('\n')
    hunks = []
    i = 0
    
    # Find start of patch
    while i < len(lines) and not lines[i].strip().startswith('*** Begin Patch'):
        i += 1
    
    if i >= len(lines):
        raise ValueError("Invalid patch: missing '*** Begin Patch'")
    
    i += 1  # Skip Begin Patch line
    
    while i < len(lines):
        line = lines[i].strip()
        
        if line.startswith('*** End Patch'):
            break
            
        elif line.startswith('*** Add File:'):
            path = line[len('*** Add File:'):].strip()
            i += 1
            content_lines = []
            
            while i < len(lines):
                if lines[i].startswith('*** '):
                    break
                if lines[i].startswith('+'):
                    content_lines.append(lines[i][1:])
                i += 1
            
            contents = '\n'.join(content_lines)
            if content_lines:
                contents += '\n'
            resolved_path = _resolve_patch_path(path, base_dir)
            hunks.append(AddFile(resolved_path, contents))
            
        elif line.startswith('*** Delete File:'):
            path = line[len('*** Delete File:'):].strip()
            resolved_path = _resolve_patch_path(path, base_dir)
            hunks.append(DeleteFile(resolved_path))
            i += 1
            
        elif line.startswith('*** Update File:'):
            path = line[len('*** Update File:'):].strip()
            i += 1

            # Check for Move to
            move_path = None
            if i < len(lines) and lines[i].strip().startswith('*** Move to:'):
                move_path = lines[i].strip()[len('*** Move to:'):].strip()
                i += 1
            
            # Parse chunks
            chunks = []
            while i < len(lines):
                if lines[i].strip().startswith('*** '):
                    break
                    
                # Parse chunk starting with @@ or plain content
                if lines[i].startswith('@@'):
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
                        if lines[i].startswith('@@') or lines[i].strip().startswith('*** '):
                            break
                        
                        if lines[i].strip() == '*** End of File':
                            is_end_of_file = True
                            i += 1
                            break
                            
                        if lines[i].startswith('-'):
                            old_lines.append(lines[i][1:])
                        elif lines[i].startswith('+'):
                            new_lines.append(lines[i][1:])
                        elif lines[i].startswith(' '):
                            # Context line - add to both
                            old_lines.append(lines[i][1:])
                            new_lines.append(lines[i][1:])
                        i += 1
                    
                    if old_lines or new_lines:
                        chunks.append(UpdateFileChunk(old_lines, new_lines, context_headers, is_end_of_file))
                else:
                    i += 1
            
            if chunks:
                resolved_path = _resolve_patch_path(path, base_dir)
                resolved_move = _resolve_patch_path(move_path, base_dir) if move_path else None
                hunks.append(UpdateFile(resolved_path, chunks, resolved_move))
        else:
            i += 1
    
    return hunks

def seek_sequence(lines: List[str], pattern: List[str], start: int, eof: bool) -> Optional[int]:
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
    
    search_start = len(lines) - len(pattern) if (eof and len(lines) >= len(pattern)) else start
    
    # Pass 1: Exact match
    for i in range(search_start, len(lines) - len(pattern) + 1):
        if lines[i:i + len(pattern)] == pattern:
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
            '\u2010': '-', '\u2011': '-', '\u2012': '-', '\u2013': '-',  # Dashes
            '\u2014': '-', '\u2015': '-', '\u2212': '-',
            '\u2018': "'", '\u2019': "'", '\u201A': "'", '\u201B': "'",  # Single quotes
            '\u201C': '"', '\u201D': '"', '\u201E': '"', '\u201F': '"',  # Double quotes
            '\u00A0': ' ', '\u2002': ' ', '\u2003': ' ', '\u2004': ' ',  # Spaces
            '\u2005': ' ', '\u2006': ' ', '\u2007': ' ', '\u2008': ' ',
            '\u2009': ' ', '\u200A': ' ', '\u202F': ' ', '\u205F': ' ',
            '\u3000': ' '
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

def apply_hunks(hunks: List[Hunk]) -> Dict[str, str]:
    """
    Apply the parsed hunks and return affected files summary.
    Returns dict with keys: 'added', 'modified', 'deleted' containing file paths
    """
    added = []
    modified = []
    deleted = []
    
    for hunk in hunks:
        if isinstance(hunk, AddFile):
            # Create parent directories if needed
            parent = os.path.dirname(hunk.path)
            if parent and not os.path.exists(parent):
                os.makedirs(parent, exist_ok=True)
            
            with open(hunk.path, 'w', encoding='utf-8') as f:
                f.write(hunk.contents)
            added.append(hunk.path)
            
        elif isinstance(hunk, DeleteFile):
            if os.path.exists(hunk.path):
                os.remove(hunk.path)
                deleted.append(hunk.path)
            else:
                raise FileNotFoundError(f"Cannot delete non-existent file: {hunk.path}")
                
        elif isinstance(hunk, UpdateFile):
            # Read original file
            with open(hunk.path, 'r', encoding='utf-8') as f:
                original = f.read()
            
            lines = original.splitlines()
            
            # Compute replacements for each chunk
            replacements = []
            line_index = 0
            
            for chunk in hunk.chunks:
                # Find where to apply this chunk
                pattern = chunk.old_lines
                found = seek_sequence(lines, pattern, line_index, chunk.is_end_of_file)
                
                # Retry without trailing empty line if not found
                if found is None and pattern and pattern[-1] == '':
                    pattern = pattern[:-1]
                    new_lines = chunk.new_lines
                    if new_lines and new_lines[-1] == '':
                        new_lines = new_lines[:-1]
                    found = seek_sequence(lines, pattern, line_index, chunk.is_end_of_file)
                else:
                    new_lines = chunk.new_lines
                
                if found is not None:
                    replacements.append((found, len(pattern), new_lines))
                    line_index = found + len(pattern)
                else:
                    # Use context headers to help locate if available
                    context_hint = f" (context: {', '.join(chunk.context_headers)})" if chunk.context_headers else ""
                    raise ValueError(f"Failed to find lines to replace in {hunk.path}{context_hint}")
            
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
            new_content = '\n'.join(lines)
            if lines and not new_content.endswith('\n'):
                new_content += '\n'
            
            if hunk.move_path:
                # Write to new location
                parent = os.path.dirname(hunk.move_path)
                if parent and not os.path.exists(parent):
                    os.makedirs(parent, exist_ok=True)
                
                with open(hunk.move_path, 'w', encoding='utf-8') as f:
                    f.write(new_content)
                
                # Remove original
                os.remove(hunk.path)
                modified.append(hunk.move_path)
            else:
                with open(hunk.path, 'w', encoding='utf-8') as f:
                    f.write(new_content)
                modified.append(hunk.path)
    
    return {
        'added': added,
        'modified': modified,
        'deleted': deleted
    }

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
            if found is None and pattern and pattern[-1] == '':
                pattern = pattern[:-1]
                new_lines = chunk.new_lines
                if new_lines and new_lines[-1] == '':
                    new_lines = new_lines[:-1]
                found = seek_sequence(lines, pattern, line_index, chunk.is_end_of_file)
            else:
                new_lines = chunk.new_lines

            if found is None:
                # If we can't find the pattern, raise to trigger fallback
                context_hint = f" (context: {', '.join(chunk.context_headers)})" if chunk.context_headers else ""
                raise ValueError(f"Failed to locate patch chunk in {upd.path}{context_hint}")

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

        new_content = '\n'.join(lines)
        if lines and not new_content.endswith('\n'):
            new_content += '\n'
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
                file_diff = list(difflib.unified_diff(
                    [],
                    new_lines,
                    fromfile="/dev/null",
                    tofile=h.path,
                    lineterm='',
                    n=3,
                ))
                combined_diff.extend(file_diff)
            elif isinstance(h, DeleteFile):
                try:
                    with open(h.path, 'r', encoding='utf-8', errors='replace') as f:
                        old_text = f.read()
                except Exception:
                    # If file doesn't exist or can't be read, show a synthetic header
                    old_text = ''
                old_lines = old_text.splitlines(keepends=True)
                file_diff = list(difflib.unified_diff(
                    old_lines,
                    [],
                    fromfile=h.path,
                    tofile="/dev/null",
                    lineterm='',
                    n=3,
                ))
                combined_diff.extend(file_diff)
            elif isinstance(h, UpdateFile):
                try:
                    with open(h.path, 'r', encoding='utf-8', errors='replace') as f:
                        original_text = f.read()
                except Exception:
                    # If we can't read the original file, we can't compute a real diff
                    # Fallback to a pseudo-diff using new lines only
                    original_text = ''

                try:
                    new_text = _simulate_update(original_text, h)
                    fromfile = h.path
                    tofile = h.move_path or h.path
                    old_lines = original_text.splitlines(keepends=True)
                    new_lines = new_text.splitlines(keepends=True)
                    file_diff = list(difflib.unified_diff(
                        old_lines,
                        new_lines,
                        fromfile=fromfile,
                        tofile=tofile,
                        lineterm='',
                        n=3,
                    ))
                    combined_diff.extend(file_diff)
                except Exception:
                    # If simulation fails, add a clear separator and include raw patch fragment
                    combined_diff.append(f"--- {h.path}")
                    combined_diff.append(f"+++ {h.move_path or h.path}")
                    # Add a marker so users know this is a fallback
                    combined_diff.append(f"@@ Unable to compute exact diff for {h.path} @@")
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
        preview = ''.join(preview_lines) if combined_diff and isinstance(combined_diff[0], str) and combined_diff[0].endswith('\n') else '\n'.join(preview_lines)

        # Count added/removed lines for metadata (skip headers)
        additions = sum(1 for l in combined_diff if isinstance(l, str) and l.startswith('+') and not l.startswith('+++'))
        deletions = sum(1 for l in combined_diff if isinstance(l, str) and l.startswith('-') and not l.startswith('---'))

        return {
            "display_type": "diff",
            "summary": summary,
            "value": {
                # Bubble Tea diff renderer expects a list of lines without newlines
                "diff_lines": [ln[:-1] if ln.endswith('\n') else ln for ln in combined_diff],
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
        preview = '\n'.join(lines[:30]) if lines else "Empty patch"
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

@rich_function_tool
def apply_patch(
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
                "metadata": {
                    "operation": "apply_patch",
                    "changes": 0
                }
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
                if found is None and pattern and pattern[-1] == '':
                    pattern = pattern[:-1]
                    new_lines = chunk.new_lines
                    if new_lines and new_lines[-1] == '':
                        new_lines = new_lines[:-1]
                    found = seek_sequence(lines, pattern, line_index, chunk.is_end_of_file)
                else:
                    new_lines = chunk.new_lines

                if found is None:
                    context_hint = f" (context: {', '.join(chunk.context_headers)})" if chunk.context_headers else ""
                    raise ValueError(f"Failed to locate patch chunk in {upd.path}{context_hint}")

                replacements.append((found, len(pattern), new_lines))
                line_index = found + len(pattern)

            # Apply replacements in reverse order
            for start_idx, old_len, new_lines in reversed(replacements):
                for _ in range(old_len):
                    if start_idx < len(lines):
                        del lines[start_idx]
                for offset, new_line in enumerate(new_lines):
                    lines.insert(start_idx + offset, new_line)

            new_content = '\n'.join(lines)
            if lines and not new_content.endswith('\n'):
                new_content += '\n'
            return new_content

        # Operation counts for summary
        adds = sum(1 for h in hunks if isinstance(h, AddFile))
        deletes = sum(1 for h in hunks if isinstance(h, DeleteFile))
        updates = sum(1 for h in hunks if isinstance(h, UpdateFile))

        combined_diff: List[str] = []

        for h in hunks:
            if isinstance(h, AddFile):
                new_lines = h.contents.splitlines(keepends=True)
                file_diff = list(difflib.unified_diff(
                    [],
                    new_lines,
                    fromfile="/dev/null",
                    tofile=h.path,
                    lineterm='',
                    n=3,
                ))
                combined_diff.extend(file_diff)
            elif isinstance(h, DeleteFile):
                try:
                    with open(h.path, 'r', encoding='utf-8', errors='replace') as f:
                        old_text = f.read()
                except Exception:
                    old_text = ''
                old_lines = old_text.splitlines(keepends=True)
                file_diff = list(difflib.unified_diff(
                    old_lines,
                    [],
                    fromfile=h.path,
                    tofile="/dev/null",
                    lineterm='',
                    n=3,
                ))
                combined_diff.extend(file_diff)
            elif isinstance(h, UpdateFile):
                try:
                    with open(h.path, 'r', encoding='utf-8', errors='replace') as f:
                        original_text = f.read()
                except Exception:
                    original_text = ''

                try:
                    new_text = _simulate_update(original_text, h)
                    fromfile = h.path
                    tofile = h.move_path or h.path
                    old_lines = original_text.splitlines(keepends=True)
                    new_lines = new_text.splitlines(keepends=True)
                    file_diff = list(difflib.unified_diff(
                        old_lines,
                        new_lines,
                        fromfile=fromfile,
                        tofile=tofile,
                        lineterm='',
                        n=3,
                    ))
                    combined_diff.extend(file_diff)
                except Exception:
                    combined_diff.append(f"--- {h.path}")
                    combined_diff.append(f"+++ {h.move_path or h.path}")
                    combined_diff.append(f"@@ Unable to compute exact diff for {h.path} @@")
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
        preview = ''.join(preview_lines) if combined_diff and isinstance(combined_diff[0], str) and combined_diff[0].endswith('\n') else '\n'.join(preview_lines)

        additions = sum(1 for l in combined_diff if isinstance(l, str) and l.startswith('+') and not l.startswith('+++'))
        deletions = sum(1 for l in combined_diff if isinstance(l, str) and l.startswith('-') and not l.startswith('---'))

        # Now apply the hunks to actually write the changes
        results = apply_hunks(hunks)

        # Build LLM-facing summary
        summary_lines = []
        if results['added']:
            summary_lines.append(f"Added {len(results['added'])} files:")
            for path in results['added']:
                summary_lines.append(f"  A {path}")
        if results['modified']:
            summary_lines.append(f"Modified {len(results['modified'])} files:")
            for path in results['modified']:
                summary_lines.append(f"  M {path}")
        if results['deleted']:
            summary_lines.append(f"Deleted {len(results['deleted'])} files:")
            for path in results['deleted']:
                summary_lines.append(f"  D {path}")

        llm_output = "Patch applied successfully.\n" + '\n'.join(summary_lines)

        total_changes = len(results['added']) + len(results['modified']) + len(results['deleted'])
        ui_metadata = {
            "display_type": "diff",
            "summary": f"Applied patch ({adds} adds, {updates} updates, {deletes} deletes) — {total_changes} file(s) changed",
            "value": {
                # Bubble Tea diff renderer expects lines without trailing newlines
                "diff_lines": [ln[:-1] if isinstance(ln, str) and ln.endswith('\n') else ln for ln in combined_diff],
                "diff_text": patch,
            },
            "preview": preview,
            "truncated": len(combined_diff) > 80,
            "metadata": {
                "operation": "apply_patch",
                "file_count": len(hunks),
                "additions": additions,
                "deletions": deletions,
                "added_files": results.get('added', []),
                "modified_files": results.get('modified', []),
                "deleted_files": results.get('deleted', []),
                "added_count": len(results.get('added', [])),
                "modified_count": len(results.get('modified', [])),
                "deleted_count": len(results.get('deleted', [])),
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
            "metadata": {
                "error_type": "patch_error",
                "error_message": str(e)
            }
        }

        return RichToolOutput(llm_output, ui_metadata)
