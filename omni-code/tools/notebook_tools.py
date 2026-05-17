import difflib
import json
import os
from typing import Any, Optional

from agents.run_context import RunContextWrapper
from omniagents.core.tools import rich_function_tool, RichToolOutput
from omniagents.core.tools import approval_metadata


def _workspace_root_from_ctx(ctx: RunContextWrapper[Any] | None) -> Optional[str]:
    try:
        if ctx is None:
            return None
        c = getattr(ctx, "context", None)
        if c is None:
            return None
        if isinstance(c, dict):
            wr = c.get("workspace_root")
        else:
            wr = getattr(c, "workspace_root", None)
        if isinstance(wr, str) and wr.strip():
            return os.path.abspath(wr)
    except Exception:
        pass
    return None


def _resolve_path(ctx: RunContextWrapper[Any] | None, path: str) -> str:
    wr = _workspace_root_from_ctx(ctx)
    if not path:
        return path
    if os.path.isabs(path):
        return path
    if wr:
        return os.path.abspath(os.path.join(wr, path))
    return path


async def _load_notebook(file_path: str) -> dict:
    """Load and parse a .ipynb file via the workspace contextvar so the
    same body works in host and sandbox modes."""
    from omniagents import workspace as _ws

    text = await _ws.read_text(file_path)
    return json.loads(text)


async def _save_notebook(file_path: str, nb: dict) -> None:
    """Write a notebook dict back via the workspace contextvar."""
    from omniagents import workspace as _ws

    text = json.dumps(nb, indent=1, ensure_ascii=False) + "\n"
    await _ws.write_text(file_path, text)


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
async def read_notebook(
    ctx: RunContextWrapper[Any],
    file_path: str,
) -> RichToolOutput:
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
    from omniagents import workspace as _ws

    file_path = _resolve_path(ctx, file_path)
    if not await _ws.exists(file_path):
        error_msg = f"File not found: {file_path}"
        return RichToolOutput(
            error_msg,
            {
                "value": error_msg,
                "display_type": "error",
                "summary": "File not found",
                "preview": error_msg,
                "truncated": False,
                "metadata": {"success": False, "error_type": "file_not_found"},
            },
        )

    if not file_path.endswith(".ipynb"):
        error_msg = f"Not a notebook file: {file_path}"
        return RichToolOutput(
            error_msg,
            {
                "value": error_msg,
                "display_type": "error",
                "summary": "Invalid file type",
                "preview": error_msg,
                "truncated": False,
                "metadata": {"success": False, "error_type": "invalid_file_type"},
            },
        )

    try:
        nb = await _load_notebook(file_path)
        cells = _get_cells(nb)

        if not cells:
            content = "Notebook is empty (no cells)."
        else:
            formatted = [_format_cell(i, cell) for i, cell in enumerate(cells)]
            content = "\n\n".join(formatted)

        preview = content[:500]
        return RichToolOutput(
            content,
            {
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
            },
        )
    except json.JSONDecodeError as e:
        error_msg = f"Invalid notebook JSON: {e}"
        return RichToolOutput(
            error_msg,
            {
                "value": error_msg,
                "display_type": "error",
                "summary": "Parse error",
                "preview": str(e),
                "truncated": False,
                "metadata": {"success": False, "error_type": "parse_error"},
            },
        )
    except Exception as e:
        error_msg = f"Error reading notebook: {e}"
        return RichToolOutput(
            error_msg,
            {
                "value": error_msg,
                "display_type": "error",
                "summary": "Read error",
                "preview": str(e),
                "truncated": False,
                "metadata": {"success": False, "error_type": "read_error"},
            },
        )


@rich_function_tool
async def edit_notebook(
    ctx: RunContextWrapper[Any],
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
    from omniagents import workspace as _ws

    file_path = _resolve_path(ctx, file_path)
    if not await _ws.exists(file_path):
        error_msg = f"File not found: {file_path}"
        return RichToolOutput(
            error_msg,
            {
                "value": error_msg,
                "display_type": "error",
                "summary": "File not found",
                "preview": error_msg,
                "truncated": False,
                "metadata": {"success": False, "error_type": "file_not_found"},
            },
        )

    if edit_mode not in ("replace", "insert", "delete"):
        error_msg = (
            f"Invalid edit_mode: {edit_mode}. Must be 'replace', 'insert', or 'delete'."
        )
        return RichToolOutput(
            error_msg,
            {
                "value": error_msg,
                "display_type": "error",
                "summary": "Invalid edit mode",
                "preview": error_msg,
                "truncated": False,
                "metadata": {"success": False, "error_type": "invalid_edit_mode"},
            },
        )

    try:
        nb = await _load_notebook(file_path)
        cells = _get_cells(nb)

        if edit_mode == "insert":
            if cell_type not in ("code", "markdown"):
                error_msg = (
                    "cell_type is required for insert and must be 'code' or 'markdown'."
                )
                return RichToolOutput(
                    error_msg,
                    {
                        "value": error_msg,
                        "display_type": "error",
                        "summary": "Missing cell_type",
                        "preview": error_msg,
                        "truncated": False,
                        "metadata": {
                            "success": False,
                            "error_type": "missing_cell_type",
                        },
                    },
                )

            if cell_number < 0 or cell_number > len(cells):
                error_msg = f"cell_number {cell_number} out of range (0-{len(cells)})."
                return RichToolOutput(
                    error_msg,
                    {
                        "value": error_msg,
                        "display_type": "error",
                        "summary": "Index out of range",
                        "preview": error_msg,
                        "truncated": False,
                        "metadata": {"success": False, "error_type": "index_error"},
                    },
                )

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
                error_msg = (
                    f"cell_number {cell_number} out of range (0-{len(cells) - 1})."
                )
                return RichToolOutput(
                    error_msg,
                    {
                        "value": error_msg,
                        "display_type": "error",
                        "summary": "Index out of range",
                        "preview": error_msg,
                        "truncated": False,
                        "metadata": {"success": False, "error_type": "index_error"},
                    },
                )

            deleted = cells.pop(cell_number)
            deleted_source = "".join(deleted.get("source", []))
            action = (
                f"Deleted cell {cell_number} ({deleted.get('cell_type', 'unknown')})"
            )
            new_source = deleted_source  # for preview

        else:  # replace
            if cell_number < 0 or cell_number >= len(cells):
                error_msg = (
                    f"cell_number {cell_number} out of range (0-{len(cells) - 1})."
                )
                return RichToolOutput(
                    error_msg,
                    {
                        "value": error_msg,
                        "display_type": "error",
                        "summary": "Index out of range",
                        "preview": error_msg,
                        "truncated": False,
                        "metadata": {"success": False, "error_type": "index_error"},
                    },
                )

            cell = cells[cell_number]
            if cell_type:
                cell["cell_type"] = cell_type
            cell["source"] = new_source.splitlines(True)
            # Clear outputs when replacing a code cell
            if cell.get("cell_type") == "code":
                cell["outputs"] = []
                cell["execution_count"] = None
            action = f"Replaced cell {cell_number} ({cell.get('cell_type', 'unknown')})"

        await _save_notebook(file_path, nb)

        preview = new_source[:300] if new_source else "(empty)"
        content = f"{action}\n\n{preview}"

        return RichToolOutput(
            content,
            {
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
            },
        )
    except json.JSONDecodeError as e:
        error_msg = f"Invalid notebook JSON: {e}"
        return RichToolOutput(
            error_msg,
            {
                "value": error_msg,
                "display_type": "error",
                "summary": "Parse error",
                "preview": str(e),
                "truncated": False,
                "metadata": {"success": False, "error_type": "parse_error"},
            },
        )
    except Exception as e:
        error_msg = f"Error editing notebook: {e}"
        return RichToolOutput(
            error_msg,
            {
                "value": error_msg,
                "display_type": "error",
                "summary": "Edit error",
                "preview": str(e),
                "truncated": False,
                "metadata": {"success": False, "error_type": "edit_error"},
            },
        )


# ---------------------------------------------------------------------------
# Approval metadata
# ---------------------------------------------------------------------------


def _load_notebook_sync_for_approval(file_path: str) -> dict:
    """Sync notebook load for the approval preview generator.

    The approval-metadata decorator is called from the sync approval-UI
    pipeline which always runs host-side, so this intentionally bypasses
    the workspace contextvar. Mirrors the analogous host-side fallback
    in omniagents' ``apply_patch_approval``.
    """
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)


@approval_metadata()
def edit_notebook_approval(
    file_path: str,
    cell_number: int,
    new_source: str,
    edit_mode: str = "replace",
    cell_type: Optional[str] = None,
) -> dict:
    """Generate approval metadata for edit_notebook showing a diff of changes."""

    basename = os.path.basename(file_path) if file_path else "notebook"

    # Try to load the notebook and show a meaningful diff
    try:
        if not os.path.exists(file_path):
            return {
                "display_type": "file_write",
                "summary": f"Edit {basename} (file not found yet)",
                "value": new_source,
                "preview": new_source[:500],
                "truncated": len(new_source) > 500,
                "metadata": {"file_path": file_path, "operation": edit_mode},
            }

        nb = _load_notebook_sync_for_approval(file_path)
        cells = _get_cells(nb)

        if edit_mode == "insert":
            # No old content to diff against — show the new cell
            return {
                "display_type": "file_write",
                "summary": f"Insert {cell_type or 'cell'} at index {cell_number} in {basename}",
                "value": new_source,
                "preview": new_source[:500],
                "truncated": len(new_source) > 500,
                "metadata": {
                    "file_path": file_path,
                    "operation": "insert",
                    "cell_number": cell_number,
                    "cell_type": cell_type,
                },
            }

        if edit_mode == "delete":
            if 0 <= cell_number < len(cells):
                old_source = "".join(cells[cell_number].get("source", []))
                old_type = cells[cell_number].get("cell_type", "unknown")
            else:
                old_source = ""
                old_type = "unknown"

            diff_lines = list(
                difflib.unified_diff(
                    old_source.splitlines(keepends=True),
                    [],
                    fromfile=f"{basename} cell {cell_number}",
                    tofile="/dev/null",
                    lineterm="",
                    n=3,
                )
            )
            preview = "".join(diff_lines[:20])
            return {
                "display_type": "diff",
                "summary": f"Delete {old_type} cell {cell_number} from {basename}",
                "value": {"diff_lines": diff_lines},
                "preview": preview,
                "truncated": len(diff_lines) > 20,
                "metadata": {
                    "file_path": file_path,
                    "operation": "delete",
                    "cell_number": cell_number,
                    "deletions": sum(
                        1
                        for l in diff_lines
                        if l.startswith("-") and not l.startswith("---")
                    ),
                },
            }

        # replace mode — diff old cell vs new content
        if 0 <= cell_number < len(cells):
            old_source = "".join(cells[cell_number].get("source", []))
        else:
            old_source = ""

        diff_lines = list(
            difflib.unified_diff(
                old_source.splitlines(keepends=True),
                new_source.splitlines(keepends=True),
                fromfile=f"{basename} cell {cell_number}",
                tofile=f"{basename} cell {cell_number}",
                lineterm="",
                n=3,
            )
        )
        preview = "".join(diff_lines[:20])
        additions = sum(
            1 for l in diff_lines if l.startswith("+") and not l.startswith("+++")
        )
        deletions = sum(
            1 for l in diff_lines if l.startswith("-") and not l.startswith("---")
        )

        return {
            "display_type": "diff",
            "summary": f"Replace cell {cell_number} in {basename}",
            "value": {
                "old_text": old_source,
                "new_text": new_source,
                "diff_lines": diff_lines,
            },
            "preview": preview,
            "truncated": len(diff_lines) > 20,
            "metadata": {
                "file_path": file_path,
                "operation": "replace",
                "cell_number": cell_number,
                "additions": additions,
                "deletions": deletions,
            },
        }

    except Exception:
        # Fallback: just show the new source
        return {
            "display_type": "file_write",
            "summary": f"{edit_mode.title()} cell {cell_number} in {basename}",
            "value": new_source,
            "preview": new_source[:500] if new_source else "(empty)",
            "truncated": len(new_source) > 500 if new_source else False,
            "metadata": {"file_path": file_path, "operation": edit_mode},
        }
