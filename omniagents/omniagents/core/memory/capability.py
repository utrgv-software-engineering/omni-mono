"""Declarative name for the runtime's memory capability."""

from typing import Literal
from agents.sandbox.capabilities.capability import Capability


class WorkplaceMemory(Capability):
    type: Literal["workplace_memory"] = "workplace_memory"
    instructions_text: str = (
        "Use memory as fallible context. Preserve provenance and respect declared store access."
    )

    async def instructions(self, manifest=None):
        return self.instructions_text
