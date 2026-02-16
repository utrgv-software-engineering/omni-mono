import json
import os
from typing import Optional

from omniagents.core.tools import rich_function_tool, RichToolOutput


def _load_notebook(file_path: str) -> dict:
    """Load and parse a .ipynb file."""
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_notebook(file_path: str, nb: dict) -> None:
    """Write a notebook dict back to disk."""
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(nb, f, indent=1, ensure_ascii=False)
        f.write("\n")


def _format_cell(index: int, cell: dict) -> str:
    """Format a single cell for display."""
    cell_type = cell.get("cell_type", "unknown")
    source = "".join(cell.get("source", []))
    cell_id = cell.get("id", "")

    header = f"--- Cell {index} [{cell_type}]"
    if cell_id:
        header += f" id={cell_id}"
    header += " ---"

    parts = [header, source]

    # Include outputs for code cells
    outputs = cell.get("outputs", [])
    if outputs:
        parts.append("")
        parts.append("Output:")
        for output in outputs:
            output_type = output.get("output_type", "")
            if output_type == "stream":
                text = "".join(output.get("text", []))
                parts.append(text.rstrip())
            elif output_type in ("execute_result", "display_data"):
                data = output.get("data", {})
                if "text/plain" in data:
                    parts.append("".join(data["text/plain"]).rstrip())
                if "text/html" in data:
                    parts.append("[HTML output]")
                if "image/png" in data:
                    parts.append("[Image output]")
            elif output_type == "error":
                ename = output.get("ename", "Error")
                evalue = output.get("evalue", "")
                parts.append(f"{ename}: {evalue}")

    return "\n".join(parts)


def _get_cells(nb: dict) -> list:
    """Get the cells list from a notebook, handling both v4 and older formats."""
    if "cells" in nb:
        return nb["cells"]
    # Notebook v3 and older use worksheets
    worksheets = nb.get("worksheets", [])
    if worksheets:
        return worksheets[0].get("cells", [])
    return []


@rich_function_tool
def read_notebook(file_path: str) -> RichToolOutput:
    """
    Read a Jupyter notebook (.ipynb) file and return all cells with their outputs.

    Returns each cell with its index, type (code/markdown), source content, and
    any outputs (for code cells). Use this to inspect notebook contents before
    editing with edit_notebook.

    Args:
        file_path: Path to the .ipynb file to read.

    Returns:
        Formatted text showing all cells with indices, types, content, and outputs.
    """
    if not os.path.exists(file_path):
        error_msg = f"File not found: {file_path}"
        return RichToolOutput(error_msg, {
            "value": error_msg,
            "display_type": "error",
            "summary": "File not found",
            "preview": error_msg,
            "truncated": False,
            "metadata": {"success": False, "error_type": "file_not_found"},
        })

    if not file_path.endswith(".ipynb"):
        error_msg = f"Not a notebook file: {file_path}"
        return RichToolOutput(error_msg, {
            "value": error_msg,
            "display_type": "error",
            "summary": "Invalid file type",
            "preview": error_msg,
            "truncated": False,
            "metadata": {"success": False, "error_type": "invalid_file_type"},
        })

    try:
        nb = _load_notebook(file_path)
        cells = _get_cells(nb)

        if not cells:
            content = "Notebook is empty (no cells)."
        else:
            formatted = [_format_cell(i, cell) for i, cell in enumerate(cells)]
            content = "\n\n".join(formatted)

        preview = content[:500]
        return RichToolOutput(content, {
            "value": content,
            "display_type": "notebook",
            "summary": f"Read {os.path.basename(file_path)} ({len(cells)} cells)",
            "preview": preview,
            "truncated": len(content) > 500,
            "metadata": {
                "success": True,
                "file_path": file_path,
                "cell_count": len(cells),
                "content_length": len(content),
            },
        })
    except json.JSONDecodeError as e:
        error_msg = f"Invalid notebook JSON: {e}"
        return RichToolOutput(error_msg, {
            "value": error_msg,
            "display_type": "error",
            "summary": "Parse error",
            "preview": str(e),
            "truncated": False,
            "metadata": {"success": False, "error_type": "parse_error"},
        })
    except Exception as e:
        error_msg = f"Error reading notebook: {e}"
        return RichToolOutput(error_msg, {
            "value": error_msg,
            "display_type": "error",
            "summary": "Read error",
            "preview": str(e),
            "truncated": False,
            "metadata": {"success": False, "error_type": "read_error"},
        })


@rich_function_tool
def edit_notebook(
    file_path: str,
    cell_number: int,
    new_source: str,
    edit_mode: str = "replace",
    cell_type: Optional[str] = None,
) -> RichToolOutput:
    """
    Edit a Jupyter notebook cell. Supports replacing, inserting, and deleting cells.

    Always use read_notebook first to see the current cell indices and contents.

    Args:
        file_path: Path to the .ipynb file to edit.
        cell_number: 0-based index of the cell to operate on.
            - For "replace": the cell at this index is replaced.
            - For "insert": a new cell is inserted at this index (existing cells shift down).
            - For "delete": the cell at this index is removed.
        new_source: The new source content for the cell. Ignored when edit_mode is "delete".
        edit_mode: One of "replace" (default), "insert", or "delete".
        cell_type: Cell type — "code" or "markdown". Required for "insert", optional for "replace"
            (defaults to existing cell type).

    Returns:
        Confirmation message with the updated cell content.
    """
    if not os.path.exists(file_path):
        error_msg = f"File not found: {file_path}"
        return RichToolOutput(error_msg, {
            "value": error_msg,
            "display_type": "error",
            "summary": "File not found",
            "preview": error_msg,
            "truncated": False,
            "metadata": {"success": False, "error_type": "file_not_found"},
        })

    if edit_mode not in ("replace", "insert", "delete"):
        error_msg = f"Invalid edit_mode: {edit_mode}. Must be 'replace', 'insert', or 'delete'."
        return RichToolOutput(error_msg, {
            "value": error_msg,
            "display_type": "error",
            "summary": "Invalid edit mode",
            "preview": error_msg,
            "truncated": False,
            "metadata": {"success": False, "error_type": "invalid_edit_mode"},
        })

    try:
        nb = _load_notebook(file_path)
        cells = _get_cells(nb)

        if edit_mode == "insert":
            if cell_type not in ("code", "markdown"):
                error_msg = "cell_type is required for insert and must be 'code' or 'markdown'."
                return RichToolOutput(error_msg, {
                    "value": error_msg,
                    "display_type": "error",
                    "summary": "Missing cell_type",
                    "preview": error_msg,
                    "truncated": False,
                    "metadata": {"success": False, "error_type": "missing_cell_type"},
                })

            if cell_number < 0 or cell_number > len(cells):
                error_msg = f"cell_number {cell_number} out of range (0-{len(cells)})."
                return RichToolOutput(error_msg, {
                    "value": error_msg,
                    "display_type": "error",
                    "summary": "Index out of range",
                    "preview": error_msg,
                    "truncated": False,
                    "metadata": {"success": False, "error_type": "index_error"},
                })

            new_cell = {
                "cell_type": cell_type,
                "source": new_source.splitlines(True),
                "metadata": {},
            }
            if cell_type == "code":
                new_cell["outputs"] = []
                new_cell["execution_count"] = None

            cells.insert(cell_number, new_cell)
            action = f"Inserted {cell_type} cell at index {cell_number}"

        elif edit_mode == "delete":
            if cell_number < 0 or cell_number >= len(cells):
                error_msg = f"cell_number {cell_number} out of range (0-{len(cells) - 1})."
                return RichToolOutput(error_msg, {
                    "value": error_msg,
                    "display_type": "error",
                    "summary": "Index out of range",
                    "preview": error_msg,
                    "truncated": False,
                    "metadata": {"success": False, "error_type": "index_error"},
                })

            deleted = cells.pop(cell_number)
            deleted_source = "".join(deleted.get("source", []))
            action = f"Deleted cell {cell_number} ({deleted.get('cell_type', 'unknown')})"
            new_source = deleted_source  # for preview

        else:  # replace
            if cell_number < 0 or cell_number >= len(cells):
                error_msg = f"cell_number {cell_number} out of range (0-{len(cells) - 1})."
                return RichToolOutput(error_msg, {
                    "value": error_msg,
                    "display_type": "error",
                    "summary": "Index out of range",
                    "preview": error_msg,
                    "truncated": False,
                    "metadata": {"success": False, "error_type": "index_error"},
                })

            cell = cells[cell_number]
            if cell_type:
                cell["cell_type"] = cell_type
            cell["source"] = new_source.splitlines(True)
            # Clear outputs when replacing a code cell
            if cell.get("cell_type") == "code":
                cell["outputs"] = []
                cell["execution_count"] = None
            action = f"Replaced cell {cell_number} ({cell.get('cell_type', 'unknown')})"

        _save_notebook(file_path, nb)

        preview = new_source[:300] if new_source else "(empty)"
        content = f"{action}\n\n{preview}"

        return RichToolOutput(content, {
            "value": content,
            "display_type": "notebook_edit",
            "summary": action,
            "preview": preview,
            "truncated": len(new_source) > 300 if new_source else False,
            "metadata": {
                "success": True,
                "file_path": file_path,
                "edit_mode": edit_mode,
                "cell_number": cell_number,
                "cell_count": len(cells),
            },
        })
    except json.JSONDecodeError as e:
        error_msg = f"Invalid notebook JSON: {e}"
        return RichToolOutput(error_msg, {
            "value": error_msg,
            "display_type": "error",
            "summary": "Parse error",
            "preview": str(e),
            "truncated": False,
            "metadata": {"success": False, "error_type": "parse_error"},
        })
    except Exception as e:
        error_msg = f"Error editing notebook: {e}"
        return RichToolOutput(error_msg, {
            "value": error_msg,
            "display_type": "error",
            "summary": "Edit error",
            "preview": str(e),
            "truncated": False,
            "metadata": {"success": False, "error_type": "edit_error"},
        })
