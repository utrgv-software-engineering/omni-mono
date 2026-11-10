#!/usr/bin/env python3
"""Read the same authenticated WebSocket snapshot used by Workplace Chat."""

import asyncio
import json
import sys
from omniagents_workplace.personal import PersonalConnection


async def main():
    mode, url, session = sys.argv[1:]
    connection = PersonalConnection(url, sys.stdin.read().strip(), session)
    try:
        snapshot = await connection.snapshot()
        if snapshot["running"] or snapshot["queue_depth"]:
            raise RuntimeError("Chat is not idle under the release lease")
        # Stable identifiers prove history survived, without exporting chat text.
        print(
            json.dumps(
                {
                    "session_id": snapshot["session_id"],
                    "items": [i["item_id"] for i in snapshot["items"]],
                }
            )
        )
    finally:
        await connection.close()


asyncio.run(main())
