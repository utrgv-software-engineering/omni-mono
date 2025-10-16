from typing import List, Dict, Any, Optional

from omniagents.core.tools import rich_function_tool, RichToolOutput


@rich_function_tool
def demo_table(rows: Optional[int] = 10) -> RichToolOutput:
    if rows is None or rows <= 0:
        rows = 10

    columns: List[Dict[str, Any]] = [
        {"title": "ID"},
        {"title": "Name"},
        {"title": "Score"},
    ]

    all_rows: List[List[str]] = []
    for i in range(1, 51):
        name = f"Item {i}"
        score = f"{(i * 7) % 100}"
        all_rows.append([str(i), name, score])

    preview_rows = all_rows[: rows]
    truncated = len(all_rows) > len(preview_rows)

    ui_metadata = {
        "display_type": "table",
        "summary": "Demo table (ID, Name, Score)",
        "truncated": truncated,
        "table": {
            "columns": columns,
            "rows": preview_rows,
            "height": min(10, max(4, rows)),
        },
        "metadata": {
            "total_rows": len(all_rows),
            "shown_rows": len(preview_rows),
        },
    }

    llm_text = "Rendered a sample table with ID, Name, and Score columns."
    if truncated:
        llm_text += f" Showing first {len(preview_rows)} of {len(all_rows)} rows."

    return RichToolOutput(llm_text, ui_metadata)
