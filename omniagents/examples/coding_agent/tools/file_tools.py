import os
import glob
import re
import difflib
from typing import List, Optional
from omniagents.core.tools import rich_function_tool, RichToolOutput, approval_metadata
from functools import lru_cache
import pygit2

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

@rich_function_tool
def glob_files(pattern: str, path: Optional[str]) -> RichToolOutput:
    """
    Finds files based on pattern matching using glob patterns.
    
    Args:
        pattern: The glob pattern to match files (e.g., "*.py", "**/*.json")
        path: The directory to search in
        
    Returns:
        Tuple of (llm_output, ui_metadata) where:
        - llm_output: Text description of files found
        - ui_metadata: Rich metadata for UI rendering
        
    Usage:
        Use this tool to find files matching specific patterns.
        Common patterns:
        - "*.py": All Python files in the current directory
        - "**/*.json": All JSON files in the current directory and subdirectories
        - "src/*.js": All JavaScript files in the src directory
        - If path is not provided, current directory "./" will be used
        
    Examples:
        # Find all Python files in the current directory
        glob_files("*.py", path="./")
        
        # Find all JSON files recursively
        glob_files("**/*.json", path="./")
        
        # Find all log files in a specific directory
        glob_files("*.log", path="/var/log")
    """
    try:
        # Default to current directory if path is not provided
        if path is None:
            path = "."
            
        # Construct the full glob pattern
        full_pattern = os.path.join(path, pattern)
        
        # Find matching files
        matches = [p for p in glob.glob(full_pattern, recursive=True) if not _is_ignored(p, path)]
        
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
            llm_output = f"No files found matching pattern '{pattern}' in {path or './'}"
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
                "scope": path or "./",
                "preview_count": len(preview_matches)
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
                "path": path
            }
        }
        
        return RichToolOutput(llm_output, ui_metadata)

@rich_function_tool
def grep_files(pattern: str, path: Optional[str], include: Optional[str]) -> RichToolOutput:
    """
    Searches for patterns in file contents using regular expressions.
    
    Args:
        pattern: The regular expression pattern to search for
        path: The directory to search in
        include: Optional file pattern to filter which files to search (e.g., "*.py")
        
    Returns:
        Tuple of (llm_output, ui_metadata) where:
        - llm_output: Text description of matches found
        - ui_metadata: Rich metadata for UI rendering
        
    Usage:
        Use this tool to search file contents for specific patterns.
        - If path is not provided, current directory "./" will be used
        - If include is not provided, all files will be searched
        
    Examples:
        # Find all occurrences of "TODO" in Python files
        grep_files("TODO", path="./", include="*.py")
        
        # Search for function definitions in the src directory
        grep_files("def\\s+\\w+\\(", path="src", include="*.py")
        
        # Find all imports in Python files
        grep_files("^import\\s+\\w+", path="./", include="*.py")
    """
    try:
        # Default to current directory if path is not provided
        if path is None:
            path = "."
            
        results = []
        total_matches = 0
        
        # Compile the regex pattern
        regex = re.compile(pattern)
        
        # Get files to search based on include pattern
        files_to_search: List[str] = []

        # Build the glob pattern based on include or default recursive search
        base_dir = path or "."
        search_pattern = os.path.join(base_dir, include if include is not None else "**")

        # Perform glob search directly to avoid relying on the decorated glob_files tool
        for candidate in glob.glob(search_pattern, recursive=True):
            if os.path.isfile(candidate) and not _is_ignored(candidate, base_dir):
                files_to_search.append(candidate)
        
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
            llm_output = f"No matches found for pattern '{pattern}' in {path or './'}" 
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
                "scope": f"{include or '*'} in {path or './'}",
                "preview_files": min(len(results), preview_limit)
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
                "pattern": pattern
            }
        }
        
        return RichToolOutput(llm_output, ui_metadata)

@rich_function_tool
def list_directory(path: str, recursive: Optional[bool]) -> RichToolOutput:
    """
    Lists files and directories in the specified path.
    
    Args:
        path: The directory path to list contents of
        recursive: Whether to list contents recursively
        
    Returns:
        Tuple of (llm_output, ui_metadata) where:
        - llm_output: Text description of directory contents
        - ui_metadata: Rich metadata for UI rendering
        
    Usage:
        Use this tool to list directory contents.
        - If recursive is not provided, it defaults to False
        
    Examples:
        # List contents of the current directory
        list_directory(".", recursive=False)
        
        # List contents of a specific directory recursively
        list_directory("/path/to/directory", recursive=True)
    """
    try:
        # Default to non-recursive if not specified
        if recursive is None:
            recursive = False
            
        files = []
        directories = []
        
        if recursive:
            for root, dirs, filenames in os.walk(path):
                # Remove ignored directories so os.walk does NOT recurse into
                # them, preventing huge listings (e.g., .venv)
                dirs[:] = [d for d in dirs if not _is_ignored(os.path.join(root, d), path)]
                
                # Add directories that are not ignored
                for dir_name in dirs:
                    dir_path = os.path.join(root, dir_name)
                    directories.append(dir_path)
                
                # Add files
                for filename in filenames:
                    file_path = os.path.join(root, filename)
                    if not _is_ignored(file_path, path):
                        files.append(file_path)
        else:
            # List only the specified directory
            with os.scandir(path) as entries:
                for entry in entries:
                    if entry.is_file():
                        if not _is_ignored(entry.path, path):
                            files.append(entry.path)
                    elif entry.is_dir():
                        if not _is_ignored(entry.path, path):
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
                "preview_count": min(total_items, preview_limit)
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
                "path": path
            }
        }
        
        return RichToolOutput(llm_output, ui_metadata)

@rich_function_tool
def read_file(file_path: str, start_line: Optional[int], num_lines: Optional[int]) -> RichToolOutput:
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
          pass the two characters backslash and n (written as "\n"). Using a newline escape inserts an actual
          newline character into the file.
        - Literal vs regex: old_text is treated literally unless regex=True. For multi-line literals, include actual
          newline characters in old_text; for inserting the literal two-character sequence backslash-n, use "\n".
        - Range semantics: start_line/end_line are 1-based and inclusive. Lines outside the range are left unchanged.
        
    Examples:
        # Replace a function name in a Python file
        edit_file("/path/to/file.py", "def old_function()", "def new_function()")
        
        # Replace a configuration value
        edit_file("/path/to/config.json", '"debug": false', '"debug": true')
        
        # Regex replacement in a specific range, dry run with expected count
        edit_file(
            "/path/to/file.go",
            old_text=r"fmt\.Fprintf\(.*\)",
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
def write_file(file_path: str, content: str, append: Optional[bool]) -> RichToolOutput:
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
        write_file("/path/to/new_file.py", "def hello_world():\n    print('Hello, world!')", append=False)
        
        # Append to a log file
        write_file("/path/to/log.txt", "New log entry\n", append=True)
    """
    try:
        # Default to overwrite if not specified
        if append is None:
            append = False
            
        # Ensure the directory exists
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
