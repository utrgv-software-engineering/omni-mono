"""HTML rendering for the task list page."""

from __future__ import annotations

import html
from typing import List

from app.store import Task


def render_task_list(tasks: List[Task]) -> str:
    """The task list page."""
    items = "\n".join(
        f'    <li class="task{" done" if t.done else ""}">{html.escape(t.title)}</li>'
        for t in tasks
    )
    return (
        "<!doctype html>\n<html><head><title>Tasks</title></head><body>\n"
        "  <h1>Tasks</h1>\n  <ul>\n" + items + "\n  </ul>\n</body></html>\n"
    )
