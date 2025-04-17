import os
import glob
import re
from typing import Dict, Any, List, Optional
from agents import function_tool

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
    try:
        # Default to current directory if path is not provided
        if path is None:
            path = "."
            
        # Construct the full glob pattern
        full_pattern = os.path.join(path, pattern)
        
        # Find matching files
        matches = glob.glob(full_pattern, recursive=True)
        
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
    try:
        # Default to current directory if path is not provided
        if path is None:
            path = "."
            
        results = []
        total_matches = 0
        
        # Compile the regex pattern
        regex = re.compile(pattern)
        
        # Get files to search based on include pattern
        files_to_search = []
        if include is not None:
            glob_result = glob_files(include, path)
            if "error" in glob_result:
                return glob_result
            files_to_search = glob_result["matches"]
        else:
            # If no include pattern, use recursive glob to find all files
            glob_result = glob_files("**", path)
            if "error" in glob_result:
                return glob_result
            files_to_search = [f for f in glob_result["matches"] if os.path.isfile(f)]
        
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
    try:
        # Default to non-recursive if not specified
        if recursive is None:
            recursive = False
            
        files = []
        directories = []
        
        if recursive:
            for root, dirs, filenames in os.walk(path):
                # Add directories
                for dir_name in dirs:
                    dir_path = os.path.join(root, dir_name)
                    directories.append(dir_path)
                
                # Add files
                for filename in filenames:
                    file_path = os.path.join(root, filename)
                    files.append(file_path)
        else:
            # List only the specified directory
            with os.scandir(path) as entries:
                for entry in entries:
                    if entry.is_file():
                        files.append(entry.path)
                    elif entry.is_dir():
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