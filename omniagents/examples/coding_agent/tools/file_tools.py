import os
import glob
import re
import subprocess
from typing import Dict, Any, List, Optional
from omniagents.core import function_tool
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


def _validate_git_environment() -> Dict[str, Any]:
    """Validate that we're in a proper Otto Code Git worktree."""
    try:
        # Check if we're in a Git repository
        result = subprocess.run([
            'git', 'rev-parse', '--is-inside-work-tree'],
            capture_output=True, text=True, cwd='.'
        )
        if result.returncode != 0:
            return {
                "valid": False,
                "error": "Not in a Git repository",
                "required_action": "Call create_git_worktree(task_slug) to set up the working environment"
            }

        # Get current branch
        branch_result = subprocess.run([
            'git', 'branch', '--show-current'],
            capture_output=True, text=True, cwd='.'
        )
        current_branch = branch_result.stdout.strip()

        if not current_branch.startswith('otto/'):
            return {
                "valid": False,
                "error": f"Not on an Otto branch. Current branch: {current_branch}",
                "required_action": "Call create_git_worktree(task_slug) to create a proper Otto worktree"
            }

        current_dir = os.path.basename(os.getcwd())
        if not current_dir.startswith('otto-'):
            return {
                "valid": False,
                "error": f"Not in an Otto worktree directory. Current: {current_dir}",
                "required_action": "Call create_git_worktree(task_slug) to create and switch to a worktree"
            }

        return {
            "valid": True,
            "branch": current_branch,
            "directory": os.getcwd(),
            "worktree_name": current_dir
        }

    except Exception as e:
        return {
            "valid": False,
            "error": f"Git validation failed: {str(e)}",
            "required_action": "Call create_git_worktree(task_slug) to set up the working environment"
        }


@function_tool
def create_git_worktree(task_slug: str, base_branch: str | None = None) -> Dict[str, Any]:
    """Create and switch to a Git worktree for the given task.

    If ``base_branch`` is ``None`` the caller's current branch is used. The tool
    creates ``otto/<slug>`` from this base and adds a worktree at
    ``../otto-<slug>`` without changing the caller's branch. The current working
    directory is changed to the new worktree on success.
    """
    try:
        import re
        from datetime import datetime

        if not task_slug or not isinstance(task_slug, str):
            return {
                "success": False,
                "error": "task_slug must be a non-empty string"
            }

        clean_slug = re.sub(r'[^a-zA-Z0-9-]', '', task_slug.lower().strip())
        clean_slug = re.sub(r'-+', '-', clean_slug).strip('-')

        if not clean_slug:
            return {
                "success": False,
                "error": "task_slug must contain at least some alphanumeric characters"
            }

        date_prefix = datetime.now().strftime("%Y%m%d")
        final_slug = f"{date_prefix}-{clean_slug}"[:50]

        branch_name = f"otto/{final_slug}"
        worktree_path = f"../otto-{final_slug}"

        if base_branch:
            clean_base = base_branch.strip()
        else:
            clean_base = subprocess.check_output(
                ['git', 'rev-parse', '--abbrev-ref', 'HEAD'], text=True
            ).strip()

        subprocess.run([
            'git', 'worktree', 'add', worktree_path,
            '-b', branch_name, clean_base
        ], check=True, cwd='.')

        # Record the base branch and commit for later diff calculations
        base_commit = subprocess.check_output(
            ['git', 'rev-parse', clean_base], text=True
        ).strip()
        os.environ['OTTO_BASE_BRANCH'] = clean_base
        os.environ['OTTO_BASE_COMMIT'] = base_commit

        abs_worktree_path = os.path.abspath(worktree_path)
        os.chdir(abs_worktree_path)

        return {
            "success": True,
            "worktree_path": abs_worktree_path,
            "branch_name": branch_name,
            "message": "Git worktree created and switched to successfully",
        }

    except subprocess.CalledProcessError as e:
        return {
            "success": False,
            "error": f"Git command failed: {e}",
            "required_action": "Ensure you're in a Git repository and have proper permissions"
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"Worktree creation failed: {str(e)}"
        }

@function_tool
def glob_files(pattern: str, path: Optional[str]) -> Dict[str, Any]:
    """
    Finds files based on pattern matching using glob patterns.
    
    Args:
        pattern: The glob pattern to match files (e.g., "*.py", "**/*.json")
        path: The directory to search in
        
    Returns:
        Dictionary containing:
        {
            "matches": List of file paths matching the pattern
            "count": Number of matches found
            "error": Error message (if any)
        }
        
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
    # SAFEGUARD: Validate Git environment first
    git_validation = _validate_git_environment()
    if not git_validation["valid"]:
        return {
            "error": f"CANNOT SEARCH FILES: {git_validation['error']}",
            "required_action": git_validation["required_action"],
            "instructions": "You MUST call the create_git_worktree tool first and then retry this operation."
        }

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
        
        return {
            "matches": matches,
            "count": len(matches)
        }
    except Exception as e:
        return {"error": f"Error finding files: {str(e)}"}

@function_tool
def grep_files(pattern: str, path: Optional[str], include: Optional[str]) -> Dict[str, Any]:
    """
    Searches for patterns in file contents using regular expressions.
    
    Args:
        pattern: The regular expression pattern to search for
        path: The directory to search in
        include: Optional file pattern to filter which files to search (e.g., "*.py")
        
    Returns:
        Dictionary containing:
        {
            "matches": List of dictionaries containing file paths and matching lines
            "count": Total number of matches found
            "error": Error message (if any)
        }
        
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
    # SAFEGUARD: Validate Git environment first
    git_validation = _validate_git_environment()
    if not git_validation["valid"]:
        return {
            "error": f"CANNOT SEARCH FILES: {git_validation['error']}",
            "required_action": git_validation["required_action"],
            "instructions": "You MUST call the create_git_worktree tool first and then retry this operation."
        }

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
        
        return {
            "matches": results,
            "count": total_matches
        }
    except Exception as e:
        return {"error": f"Error searching files: {str(e)}"}

@function_tool
def list_directory(path: str, recursive: Optional[bool]) -> Dict[str, Any]:
    """
    Lists files and directories in the specified path.
    
    Args:
        path: The directory path to list contents of
        recursive: Whether to list contents recursively
        
    Returns:
        Dictionary containing:
        {
            "files": List of file paths
            "directories": List of directory paths
            "count": Total number of entries
            "error": Error message (if any)
        }
        
    Usage:
        Use this tool to list directory contents.
        - If recursive is not provided, it defaults to False
        
    Examples:
        # List contents of the current directory
        list_directory(".", recursive=False)
        
        # List contents of a specific directory recursively
        list_directory("/path/to/directory", recursive=True)
    """
    if path != "." or recursive is True:
        git_validation = _validate_git_environment()
        if not git_validation["valid"]:
            return {
                "error": f"CANNOT LIST DIRECTORY: {git_validation['error']}",
                "required_action": git_validation["required_action"],
                "instructions": "You MUST call the create_git_worktree tool first and then retry this operation."
            }

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
        
        return {
            "files": files,
            "directories": directories,
            "count": len(files) + len(directories)
        }
    except Exception as e:
        return {"error": f"Error listing directory: {str(e)}"}

@function_tool
def read_file(file_path: str, start_line: Optional[int], num_lines: Optional[int]) -> Dict[str, Any]:
    """
    Reads the contents of a file.
    
    Args:
        file_path: Path to the file to read
        start_line: Line number to start reading from (0-indexed)
        num_lines: Maximum number of lines to read
        
    Returns:
        Dictionary containing:
        {
            "content": The file content as a string
            "lines": List of lines in the file
            "line_count": Total number of lines read
            "error": Error message (if any)
        }
        
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
    git_validation = _validate_git_environment()
    if not git_validation["valid"]:
        return {
            "error": f"CANNOT READ FILE: {git_validation['error']}",
            "required_action": git_validation["required_action"],
            "instructions": "You MUST call the create_git_worktree tool first and then retry this operation."
        }

    if os.path.isabs(file_path):
        return {
            "error": "CANNOT READ FILE: Absolute paths not allowed in worktree",
            "instructions": f"Use relative path instead. Remove leading slash from: {file_path}"
        }

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
            
            return {
                "content": "".join(selected_lines),
                "lines": selected_lines,
                "line_count": len(selected_lines)
            }
    except Exception as e:
        return {"error": f"Error reading file: {str(e)}"}

@function_tool
def edit_file(file_path: str, old_text: str, new_text: str) -> Dict[str, Any]:
    """
    Makes targeted edits to a specific file by replacing text.
    
    Args:
        file_path: Path to the file to edit
        old_text: The text to replace
        new_text: The replacement text
        
    Returns:
        Dictionary containing:
        {
            "success": Boolean indicating if the edit was successful
            "replacements": Number of replacements made
            "error": Error message (if any)
        }
        
    Usage:
        Use this tool to make specific text replacements in files.
        
    Examples:
        # Replace a function name in a Python file
        edit_file("/path/to/file.py", "def old_function()", "def new_function()")
        
        # Replace a configuration value
        edit_file("/path/to/config.json", '"debug": false', '"debug": true')
    """
    git_validation = _validate_git_environment()
    if not git_validation["valid"]:
        return {
            "error": f"CANNOT EDIT FILE: {git_validation['error']}",
            "required_action": git_validation["required_action"],
            "instructions": "You MUST call the create_git_worktree tool first and then retry this operation."
        }

    if os.path.isabs(file_path):
        return {
            "error": "CANNOT EDIT FILE: Absolute paths not allowed in worktree",
            "instructions": f"Use relative path instead. Remove leading slash from: {file_path}"
        }

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
            
            return {
                "success": True,
                "replacements": replacements
            }
        else:
            return {
                "success": False,
                "replacements": 0,
                "error": "No matches found for the specified text"
            }
    except Exception as e:
        return {"error": f"Error editing file: {str(e)}"}

@function_tool
def write_file(file_path: str, content: str, append: Optional[bool]) -> Dict[str, Any]:
    """
    Creates a new file or overwrites an existing file with the provided content.
    
    Args:
        file_path: Path to the file to write
        content: Content to write to the file
        append: Whether to append to the file instead of overwriting
        
    Returns:
        Dictionary containing:
        {
            "success": Boolean indicating if the write was successful
            "bytes_written": Number of bytes written
            "error": Error message (if any)
        }
        
    Usage:
        Use this tool to create or modify files.
        - If append is not provided, it defaults to False (overwrite mode)
        
    Examples:
        # Create a new Python file
        write_file("/path/to/new_file.py", "def hello_world():\n    print('Hello, world!')", append=False)
        
        # Append to a log file
        write_file("/path/to/log.txt", "New log entry\n", append=True)
    """
    git_validation = _validate_git_environment()
    if not git_validation["valid"]:
        return {
            "error": f"CANNOT WRITE FILE: {git_validation['error']}",
            "required_action": git_validation["required_action"],
            "instructions": "You MUST call the create_git_worktree tool first and then retry this operation."
        }

    if os.path.isabs(file_path):
        return {
            "error": "CANNOT WRITE FILE: Absolute paths not allowed in worktree",
            "instructions": f"Use relative path instead. Remove leading slash from: {file_path}"
        }

    try:
        # Default to overwrite if not specified
        if append is None:
            append = False
            
        # Ensure the directory exists
        os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)
        
        # Write mode: 'a' for append, 'w' for overwrite
        mode = 'a' if append else 'w'
        
        with open(file_path, mode, encoding='utf-8') as f:
            bytes_written = f.write(content)
        
        return {
            "success": True,
            "bytes_written": bytes_written
        }
    except Exception as e:
        return {"error": f"Error writing file: {str(e)}"}