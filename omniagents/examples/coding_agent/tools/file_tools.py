import os
import glob
import re
from typing import Dict, Any, List, Optional
from omniagents.core.tools import rich_function_tool, function_tool, RichToolOutput
import pathlib
from functools import lru_cache

# Optional dependency: pathspec is lightweight and allows us to honor .gitignore
# If it is not available at runtime we simply fall back to previous behaviour.
try:
    import pathspec  # type: ignore
except ImportError:  # pragma: no cover – pathspec should be present via pyproject
    pathspec = None
    
def _load_gitignore_spec(start_dir: str = "."):
    """Return a PathSpec compiled from every .gitignore under *start_dir*.

    If pathspec is not installed or no .gitignore files are found, returns None.
    The resulting matcher uses Git's ``gitwildmatch`` syntax.
    """
    if pathspec is None:
        return None

    patterns: list[str] = []

    # 1) Collect .gitignore files from *start_dir* upward to filesystem root.
    current = os.path.abspath(start_dir)
    while True:
        candidate = os.path.join(current, ".gitignore")
        if os.path.isfile(candidate):
            try:
                with open(candidate, "r", encoding="utf-8", errors="ignore") as fh:
                    for raw in fh:
                        stripped = raw.rstrip("\n")
                        if stripped.strip() == "" or stripped.lstrip().startswith("#"):
                            continue
                        if stripped in {"*", "/*"}:
                            continue
                        patterns.append(stripped)
            except OSError:
                pass

        parent = os.path.dirname(current)
        if parent == current:
            break  # reached filesystem root
        current = parent

    # 2) Also walk downward from *start_dir*, but avoid large ignored folders to
    #    pick up nested .gitignore rules that matter (e.g., in sub-modules).
    for root, dirs, files in os.walk(start_dir):
        for ignore_dir in [".git", ".venv", "venv", "env", "ENV"]:
            if ignore_dir in dirs:
                dirs.remove(ignore_dir)
        if ".gitignore" in files and root != start_dir:  # we've already read start_dir above
            gitignore_path = os.path.join(root, ".gitignore")
            try:
                with open(gitignore_path, "r", encoding="utf-8", errors="ignore") as fh:
                    for raw in fh:
                        stripped = raw.rstrip("\n")
                        if stripped.strip() == "" or stripped.lstrip().startswith("#"):
                            continue
                        if stripped in {"*", "/*"}:
                            continue
                        # Prefix nested rules with the sub-directory so they
                        # evaluate correctly relative to the repo root.
                        rel_prefix = os.path.relpath(root, start_dir)
                        patterns.append(os.path.join(rel_prefix, stripped))
            except OSError:
                pass

    if not patterns:
        return None

    try:
        return pathspec.PathSpec.from_lines("gitwildmatch", patterns)
    except Exception:
        # Any parsing error falls back to None
        return None


@lru_cache(maxsize=4)
def _get_gitignore_spec(start_dir: str = "."):
    """Cached wrapper for _load_gitignore_spec."""
    return _load_gitignore_spec(start_dir)


def _is_ignored(path: str, repo_root: str = ".") -> bool:
    """Return True if *path* matches any .gitignore pattern."""
    spec = _get_gitignore_spec(repo_root)
    if spec is None:
        return False
    # pathspec expects POSIX style relative paths
    try:
        rel_path = os.path.relpath(path, repo_root)
    except ValueError:
        # path on different drive etc.
        return False
    matched = spec.match_file(rel_path)
    if not matched and os.path.isdir(path):
        # Some patterns in .gitignore are written with a trailing slash, which
        # pathspec expects to see in the candidate path. For directories we
        # retry with an appended slash so that a rule like ".venv/" correctly
        # matches the ".venv" folder itself.
        matched = spec.match_file(rel_path + "/")
    return matched

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
        start_line: Line number to start reading from (0-indexed)
        num_lines: Maximum number of lines to read
        
    Returns:
        Tuple of (llm_output, ui_metadata) where:
        - llm_output: The file content as a string
        - ui_metadata: Rich metadata for UI rendering
        
    Usage:
        Use this tool to read the contents of files.
        - If start_line is not provided, it defaults to 0 (first line)
        - If num_lines is not provided, all remaining lines will be read
        
    Examples:
        # Read an entire file
        read_file("/path/to/file.txt", start_line=0, num_lines=null)
        
        # Read 10 lines starting from line 5
        read_file("/path/to/file.txt", start_line=5, num_lines=10)
    """
    try:
        # Default to start at beginning if not specified
        if start_line is None:
            start_line = 0
            
        with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
            # Read all lines
            all_lines = f.readlines()
            
            # Apply start_line and num_lines filters
            if start_line < 0:
                start_line = 0
            
            if start_line >= len(all_lines):
                start_line = len(all_lines) - 1 if all_lines else 0
            
            if num_lines is None:
                # Read all remaining lines
                selected_lines = all_lines[start_line:]
            else:
                # Read specified number of lines
                selected_lines = all_lines[start_line:start_line + num_lines]
            
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
def edit_file(file_path: str, old_text: str, new_text: str) -> RichToolOutput:
    """
    Makes targeted edits to a specific file by replacing text.
    
    Args:
        file_path: Path to the file to edit
        old_text: The text to replace
        new_text: The replacement text
        
    Returns:
        Tuple of (llm_output, ui_metadata) where:
        - llm_output: Description of edit result
        - ui_metadata: Rich metadata for UI rendering
        
    Usage:
        Use this tool to make specific text replacements in files.
        
    Examples:
        # Replace a function name in a Python file
        edit_file("/path/to/file.py", "def old_function()", "def new_function()")
        
        # Replace a configuration value
        edit_file("/path/to/config.json", '"debug": false', '"debug": true')
    """
    try:
        # Read the file content
        with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
        
        # Replace the text
        new_content, replacements = re.subn(re.escape(old_text), new_text, content)
        
        if replacements > 0:
            # Write the modified content back to the file
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(new_content)
            
            # Create a simple diff preview
            old_preview = old_text[:100] + ("..." if len(old_text) > 100 else "")
            new_preview = new_text[:100] + ("..." if len(new_text) > 100 else "")
            preview = f"- {old_preview}\n+ {new_preview}"
            
            # LLM output
            llm_output = f"Successfully updated {os.path.basename(file_path)} - made {replacements} replacement(s)"
            
            # UI metadata
            ui_metadata = {
                "value": f"Made {replacements} replacement(s)",  # The actual result
                "display_type": "file_edit",
                "summary": f"Updated {os.path.basename(file_path)} ({replacements} replacements)",
                "preview": preview,
                "truncated": len(old_text) > 100 or len(new_text) > 100,
                "metadata": {
                    "file_path": file_path,
                    "operation": "edit",
                    "changes_made": replacements,
                    "replacements": replacements
                }
            }
            
            return RichToolOutput(llm_output, ui_metadata)
        else:
            llm_output = f"No matches found for the specified text in {file_path}"
            
            ui_metadata = {
                "value": "No matches found for the specified text",
                "display_type": "error",
                "summary": "Edit failed - text not found",
                "preview": "No matches found for the specified text",
                "truncated": False,
                "metadata": {
                    "error_type": "no_match",
                    "file_path": file_path
                }
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