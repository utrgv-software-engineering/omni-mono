"""HTTP handlers. Each returns (status, JSON-ready body)."""

from __future__ import annotations

import json
from typing import Any, Dict, Tuple

from app.store import Store

Response = Tuple[int, Dict[str, Any]]


def list_tasks(store: Store, query: Dict[str, str]) -> Response:
    """GET /tasks."""
    return 200, {"tasks": [t.to_dict() for t in store.all()]}


def get_task(store: Store, task_id: str) -> Response:
    """GET /tasks/<id>."""
    if not task_id.isdigit():
        return 404, {"error": "not found"}
    task = store.get(int(task_id))
    if task is None:
        return 404, {"error": "not found"}
    return 200, task.to_dict()


def create_task(store: Store, body: bytes) -> Response:
    """POST /tasks."""
    try:
        data = json.loads(body or b"{}")
    except json.JSONDecodeError:
        return 400, {"error": "body must be JSON"}
    title = data.get("title") if isinstance(data, dict) else None
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 200:
        return 400, {"error": "title must be 1-200 characters"}
    return 201, store.create(title.strip()).to_dict()
