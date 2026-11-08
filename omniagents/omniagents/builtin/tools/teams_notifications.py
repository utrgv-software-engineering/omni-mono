import os
from typing import Any

import httpx
from agents.run_context import RunContextWrapper

from omniagents.core.tools.discovery import function_tool


@function_tool
async def teams_notify(
    ctx: RunContextWrapper[Any], event_id: str, title: str, text: str
) -> dict:
    """Post an Adaptive Card to the factory's configured Teams channel.

    Use only when an automation or user asks for a Teams notification. Supply a
    stable source event ID and reuse it with identical content on retries.
    No Workplace page path is needed. Automatic event notifications attach
    their own deep links. Keep confidential content out of channel summaries.
    Accepted means the Workflow accepted the request, not confirmed channel
    delivery. Unknown/sending must be checked by a person before resending;
    never invent a new event ID to bypass duplicate protection.
    """
    url = os.environ.get("FACTORY_TEAMS_NOTIFY_URL", "")
    token = os.environ.get("FACTORY_SETTINGS_TOKEN", "")
    if not url or not token:
        raise ValueError("This factory has not configured Teams notifications")
    try:
        async with httpx.AsyncClient(
            timeout=45, trust_env=False, follow_redirects=False
        ) as client:
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                json={"event_id": event_id, "title": title, "text": text},
            )
    except httpx.HTTPError:
        return {
            "status": "unknown",
            "detail": "Workplace response unavailable; retry with the same event ID and content",
        }
    if response.status_code != 200:
        return {
            "status": "failed",
            "http_status": response.status_code,
            "detail": "Workplace rejected the notification",
        }
    return response.json()
