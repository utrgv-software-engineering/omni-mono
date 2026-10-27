"""Durable Workplace Library artifacts, edited with the ordinary file tools."""

import os
from typing import Any, Literal

import httpx
from agents.run_context import RunContextWrapper

from omniagents.core.tools.artifacts import emit_artifact
from omniagents.core.tools.discovery import function_tool


async def _request(method, path, **kwargs):
    url = os.environ.get("LIBRARY_API_URL", "").rstrip("/")
    token = os.environ.get("CANVAS_MCP_TOKEN", "")
    if not url or not token:
        raise ValueError("This host has not configured the Workplace Library")
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.request(
            method, url + path, headers={"Authorization": f"Bearer {token}"}, **kwargs
        )
        response.raise_for_status()
        return response.json()


@function_tool
async def artifact_create(
    ctx: RunContextWrapper[Any],
    title: str,
    kind: Literal["page", "document"] = "document",
) -> dict:
    """Create a persistent Library page (index.html) or document (index.md).

    Write the returned main file using normal file tools, then call artifact_show.
    The folder is persistent across runs and sandbox restarts. HTML may use local
    relative CSS, scripts, images and data files; external network, forms and
    parent-page access are blocked. Keep pages responsive and self-contained.
    Each file is limited to 2 MiB. Prefer Canvas tools for multi-board mockups.
    """
    item = await _request("POST", "/artifacts", json={"title": title, "kind": kind})
    return {k: item[k] for k in ("id", "title", "kind", "folder", "main", "url")}


@function_tool
async def artifact_list(ctx: RunContextWrapper[Any]) -> dict:
    """List saved Library pages/documents with their IDs and writable folders.

    Use Canvas's list_canvases tool for design canvases; both appear in Library.
    """
    result = await _request("GET", "/artifacts")
    return {
        "artifacts": [
            {k: a[k] for k in ("id", "title", "kind", "folder", "main", "url")}
            for a in result["artifacts"]
        ]
    }


@function_tool
async def artifact_show(ctx: RunContextWrapper[Any], artifact: str) -> dict:
    """Show a saved artifact in chat with an Open in Library link. Pass its ID.

    Write its files first. Later changes use the same folder and Library entry.
    """
    from urllib.parse import quote

    item = await _request("GET", "/artifacts/" + quote(artifact, safe=""))
    preview = item.get("markdown", "")[:1200]
    content = f"{preview}\n\n[Open in Library](<{item['url']}>)"
    emit_artifact(item["title"], content, "markdown", f"library:{item['id']}")
    return {"id": item["id"], "title": item["title"], "url": item["url"]}
