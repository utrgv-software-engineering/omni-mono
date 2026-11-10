"""Explicit presentation requests, carried by the existing artifact stream."""

import json
import time
import uuid
from .artifacts import emit_artifact


def present_view(kind, title, *, id=None, board=None):
    value = {
        "kind": kind,
        "title": title,
        "presentation_id": uuid.uuid4().hex,
        "issued_at": time.time() * 1000,
    }
    if id is not None:
        value["id"] = id
    if board is not None:
        value["board"] = board
    emit_artifact(
        title, json.dumps(value), "workplace_view", f"{kind}:{id or 'desktop'}"
    )
    return value
