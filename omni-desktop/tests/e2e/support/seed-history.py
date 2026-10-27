"""Write persisted chat history through omniagents' own recorder.

Usage: seed-history.py <thread_id>:<title>:<prefix> ... (OMNIAGENTS_HOME set)

Each thread gets a first turn prompted by <title> (which becomes its sidebar
title) followed by HISTORY_COUNT completed turns prompted <prefix>_0000...,
so the app boots onto conversations that look like long saved chats without
running a model turn per message.
"""

import sys

from omniagents.core.conversation.recorder import ConversationRecorder
from omniagents.core.conversation.store import get_conversation_store

HISTORY_COUNT = 750
# The launcher's chat agent shard (sessions/omni_code/omni).
PROJECT_SLUG = "omni_code"
AGENT_SLUG = "omni"
# The launcher's single serve token authenticates every channel as this user.
USER_ID = "token_user"


def seed(recorder: ConversationRecorder, thread_id: str, prompts: list[str]) -> None:
    for index, prompt in enumerate(prompts):
        turn_id = f"seed-{thread_id}-{index}"
        recorder.open_turn(
            thread_id, turn_id, prompt=prompt, prompt_role="user", user_id=USER_ID
        )
        recorder.close_turn(
            thread_id,
            turn_id,
            end_reason="completed",
            error=None,
            usage=None,
            model=None,
            model_ref=None,
        )


def main(specs: list[str]) -> None:
    store = get_conversation_store(PROJECT_SLUG, AGENT_SLUG)
    recorder = ConversationRecorder(
        store, project_slug=PROJECT_SLUG, agent_slug=AGENT_SLUG
    )
    for spec in specs:
        thread_id, title, prefix = spec.split(":")
        history = [f"{prefix}_{index:04d}" for index in range(HISTORY_COUNT)]
        seed(recorder, thread_id, [title, *history])


if __name__ == "__main__":
    main(sys.argv[1:])
