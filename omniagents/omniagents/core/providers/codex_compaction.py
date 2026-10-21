"""Adapt SDK compaction to Codex's streamed Responses compaction protocol."""

from types import SimpleNamespace
from typing import Any
from urllib.parse import urlparse

from omniagents.core.session.compaction_retry import (
    TRANSIENT_ERROR_CODES,
    TransientCompactionError,
)


def is_codex_client(client: Any) -> bool:
    url = urlparse(str(getattr(client, "base_url", "")))
    return (
        url.hostname in {"chatgpt.com", "chat.openai.com"}
        and url.path.rstrip("/") == "/backend-api/codex"
    )


class CodexCompactionClient:
    """Compaction-only facade; reuse the authenticated client's HTTP hooks.

    Codex sends a compaction_trigger through /responses and expects exactly
    one encrypted compaction item before response.completed. The SDK session
    can still own triggering and atomic persistence through this facade.
    """

    def __init__(self, client: Any):
        self._client = client
        self.responses = self

    def with_options(self, **kwargs: Any) -> "CodexCompactionClient":
        return type(self)(self._client.with_options(**kwargs))

    async def compact(self, *, model: str, input: list[Any]) -> Any:
        # Never append the request control to durable session history.
        stream = await self._client.responses.create(
            model=model,
            instructions="",
            input=[*input, {"type": "compaction_trigger"}],
            store=False,
            stream=True,
            include=["reasoning.encrypted_content"],
        )
        output = []
        completed = False
        try:
            async for event in stream:
                if event.type == "response.output_item.done":
                    item = event.item.model_dump(exclude_unset=True, warnings=False)
                    if item.get("type") in {"compaction", "compaction_summary"}:
                        item["type"] = "compaction"
                        output.append(item)
                elif event.type == "response.completed":
                    completed = True
                    break
                elif event.type in {"error", "response.failed", "response.incomplete"}:
                    details = event.model_dump(exclude_unset=True, warnings=False)
                    error = (
                        details.get("error")
                        or (details.get("response") or {}).get("error")
                        or details
                    )
                    if error.get("code") in TRANSIENT_ERROR_CODES:
                        raise TransientCompactionError(
                            "Codex compaction temporary server failure"
                        )
                    raise RuntimeError(f"Codex compaction failed: {event.type}")
        finally:
            await stream.close()

        if not completed:
            raise TransientCompactionError(
                "Codex compaction stream ended before response.completed"
            )
        if (
            len(output) != 1
            or not isinstance(output[0].get("encrypted_content"), str)
            or not output[0]["encrypted_content"]
        ):
            raise RuntimeError(
                "Codex compaction expected exactly one encrypted compaction item"
            )

        # Like Codex, keep user instructions alongside the encrypted summary.
        # Omni also keeps explicit system/developer messages: these may carry
        # session instructions that are not supplied in the next request's
        # top-level instructions. Tool results and old summaries are replaced.
        retained = [
            item
            for item in input
            if isinstance(item, dict)
            and item.get("type", "message") == "message"
            and item.get("role") in {"user", "system", "developer"}
        ]
        return SimpleNamespace(output=[*retained, *output])
